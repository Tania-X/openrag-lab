"""Read-only reconciliation report for the document registry (design §7).

The registry can disagree with OpenRAG in ways no single request can see: a row
left ``INDEXING`` by a killed process, a delete that OpenRAG refused, a tombstone
whose removal was never confirmed, a document that exists remotely with no row
at all. Every one of those is *discoverable* (that is what the state machine is
for) but nothing looks at them yet.

**This stage reports and stops.** It writes nothing and repairs nothing:

* an automatic repairer is an authority amplifier — it would turn a wrong guess
  into a persisted fact at scale, so it ships in report mode first and stays
  there until the report has been read for a while (decision 5);
* every row it cannot settle is evidence about the *remote* side, and the remote
  is exactly what this process cannot see without asking.

The classification is a pure function of rows + thresholds + remote names, so it
can be tested without a database or a network, and the mutations that matter
(an off-by-one threshold, a forgotten status, a namespace that does not match)
are cheap to pin.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from anyio import to_thread

from openrag_lab.config import ConfigurationError
from openrag_lab.domain.identity.models import Document
from openrag_lab.domain.shared.enums import DocumentStatus
from openrag_lab.domain.shared.errors import NotFoundError

logger = logging.getLogger(__name__)

#: ``INDEXING`` is legitimate for as long as an upload may take, and the intent
#: row is committed *before* the concurrency limiter is acquired — so its age
#: covers queue wait **plus** one full ingest wait. Twice the configured ingest
#: timeout is therefore the derivation, not a round number: a smaller threshold
#: would flag uploads that are still perfectly healthy (and a false positive
#: here is expensive: acting on it could promote a half-written document).
INDEXING_STALE_FACTOR = 2

#: A ``DELETING`` row can only be left behind by a crash or a cancellation
#: *during* one HTTP round trip, so its threshold only has to clear one request.
DELETING_STALE_FACTOR = 2

#: Finding categories. Strings (not an enum) because they are report labels:
#: they go into a rendered table an operator reads, not into the database.
CATEGORY_STUCK_UPLOAD = "stuck-upload"
CATEGORY_STUCK_DELETE = "stuck-delete"
CATEGORY_FAILED = "failed"
CATEGORY_UNCONFIRMED_DELETE = "unconfirmed-delete"
CATEGORY_INCONSISTENT = "inconsistent"

#: What an operator (or the future `--fix` stage) should do about each category.
#: Kept next to the classification so the advice cannot drift from the reason.
ACTION_STUCK_UPLOAD = "probe OpenRAG: present -> promote to indexed, absent -> mark failed"
ACTION_STUCK_DELETE = "retry the remote delete (a 404 counts as success)"
ACTION_FAILED = "re-uploading the same name retries on this row"
ACTION_FAILED_UNKNOWN = "remote state unknown: probe first, then retry or mark failed"
ACTION_UNCONFIRMED_DELETE = "retry the remote delete (a 404 counts as success)"
ACTION_INCONSISTENT = (
    "this row records a verdict-less outcome while claiming a state that has one"
    " — check the write path (or the row itself); nothing here repairs it"
)

#: The states that may carry ``remote_outcome_unknown``: both mean "concluded
#: without a verdict from OpenRAG". Anywhere else the flag contradicts the
#: status, and that contradiction is reported rather than swallowed.
STATES_THAT_MAY_LACK_A_VERDICT = (DocumentStatus.FAILED, DocumentStatus.DELETED)


@dataclass(frozen=True, slots=True)
class StalenessRules:
    """Age thresholds, derived from the timeouts they guard (never hand-set)."""

    indexing_seconds: float
    deleting_seconds: float

    @classmethod
    def derive(
        cls,
        *,
        ingest_timeout_seconds: float,
        request_timeout_seconds: float,
    ) -> StalenessRules:
        """Build the rules from the budgets they are derived from.

        Taking the timeouts as arguments (rather than reading settings here)
        keeps the derivation visible and lets the tests move one input at a time.
        """
        return cls(
            indexing_seconds=ingest_timeout_seconds * INDEXING_STALE_FACTOR,
            deleting_seconds=request_timeout_seconds * DELETING_STALE_FACTOR,
        )


@dataclass(frozen=True, slots=True)
class Finding:
    """One registry row that needs attention, with its suggested action.

    ``action`` and ``reason`` stay separate on purpose: the action is what this
    report *recommends* (a fixed phrase per category), the reason is what the row
    actually recorded. Merging them would make the recommendation look like
    evidence.
    """

    tenant_slug: str
    stored_filename: str
    status: DocumentStatus
    age_seconds: float
    category: str
    action: str
    reason: str | None
    remote_outcome_unknown: bool


@dataclass(frozen=True, slots=True)
class RemoteComparison:
    """How the remote listing disagrees with the registry's names."""

    ghosts: list[str]
    """Remote documents no row mentions (design §2 case A)."""

    missing: list[str]
    """Rows the registry calls ``INDEXED`` whose content is not remote (case B)."""

    unknown_namespace: list[str]
    """Remote documents outside every known tenant namespace (e.g. pre-namespace
    leftovers, which no tenant's search scope can reach)."""


@dataclass(frozen=True, slots=True)
class RemoteUnavailable:
    """A tenant whose remote listing could not be read.

    Recorded rather than raised: this command exists for the moments when the
    system is misbehaving, and "OpenRAG is down" is one of the things an operator
    most needs to see. The local half of the report needs no network at all, so
    it still gets printed.
    """

    tenant_slug: str
    detail: str


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    findings: list[Finding]
    remote: RemoteComparison
    rules: StalenessRules
    remote_errors: list[RemoteUnavailable] = field(default_factory=list)
    #: A deployment problem, not a remote failure. Kept apart because the two
    #: send an operator to completely different places: a missing API key is
    #: fixed in this deployment's configuration, while an unreachable OpenRAG is
    #: fixed on OpenRAG's side. Folding the first into the second is how
    #: "misconfigured" gets misread as "the service is down".
    configuration_problem: str | None = None

    @property
    def needs_attention(self) -> bool:
        """True when anything at all was reported (drives ``--strict``)."""
        return bool(
            self.findings
            or self.remote.ghosts
            or self.remote.missing
            or self.remote.unknown_namespace
            or self.remote_errors
            or self.configuration_problem
        )


def classify_registry(
    rows: list[Document],
    *,
    tenant_slug: str,
    now: datetime,
    rules: StalenessRules,
) -> list[Finding]:
    """Turn candidate rows into findings, oldest first.

    The input is a *candidate superset*: ``list_unsettled`` selects anything
    transitional, failed or flagged, and this function decides. Rows that turn
    out to be fine (a fresh in-flight operation) are dropped on purpose — that
    is the age gate, not a silent failure.

    What must **not** be dropped is a contradiction: a row whose flag says "no
    verdict was received" while its status claims a verdict (``INDEXED``), or a
    flag on a transitional state where only ``mark_failed``/``mark_deleted`` can
    legitimately set it. Those are unreachable through the current write paths,
    which is exactly why they are reported instead of assumed away — if a future
    write path introduces one, the report is where it should surface.

    Age decides whether a transitional row is stuck: the state alone cannot,
    because "in flight" and "abandoned" look identical from the row.
    """
    findings: list[Finding] = []
    for document in rows:
        age = (now - document.updated_at).total_seconds()
        category: str | None = None
        action: str | None = None
        if (
            document.remote_outcome_unknown
            and document.status not in STATES_THAT_MAY_LACK_A_VERDICT
        ):
            # Checked first: it outranks the age gate, so a flagged transitional
            # row cannot hide behind "it may still be in flight".
            category, action = CATEGORY_INCONSISTENT, ACTION_INCONSISTENT
        elif document.status is DocumentStatus.INDEXING:
            if age > rules.indexing_seconds:
                category, action = CATEGORY_STUCK_UPLOAD, ACTION_STUCK_UPLOAD
        elif document.status is DocumentStatus.DELETING:
            if age > rules.deleting_seconds:
                category, action = CATEGORY_STUCK_DELETE, ACTION_STUCK_DELETE
        elif document.status is DocumentStatus.FAILED:
            category = CATEGORY_FAILED
            action = (
                ACTION_FAILED_UNKNOWN
                if document.remote_outcome_unknown
                else ACTION_FAILED
            )
        elif document.status is DocumentStatus.DELETED:
            # A confirmed tombstone needs nothing: it is the record that the
            # delete was finished. Only an unconfirmed one owes a retry.
            if document.remote_outcome_unknown:
                category, action = (
                    CATEGORY_UNCONFIRMED_DELETE,
                    ACTION_UNCONFIRMED_DELETE,
                )
        if category is None or action is None:
            continue
        findings.append(
            Finding(
                tenant_slug=tenant_slug,
                stored_filename=document.stored_filename,
                status=document.status,
                age_seconds=age,
                category=category,
                action=action,
                reason=document.status_reason,
                remote_outcome_unknown=document.remote_outcome_unknown,
            )
        )
    findings.sort(key=lambda finding: finding.age_seconds, reverse=True)
    return findings


def compare_remote(
    *,
    registered: set[str],
    indexed: set[str],
    remote: list[str],
    namespaces: dict[str, str],
) -> RemoteComparison:
    """Compare a remote listing against the registry's names.

    ``namespaces`` maps a filename prefix (``"acme/"``) to its tenant slug, so a
    remote name can be attributed before it is called a ghost. ``registered``
    holds *every* name the registry knows — including failed rows and tombstones:
    a row is a record, and a document we know about is not a ghost.
    """
    remote_names = set(remote)
    ghosts: list[str] = []
    unknown: list[str] = []
    for name in sorted(remote_names):
        prefix = _namespace_of(name, namespaces)
        if prefix is None:
            unknown.append(name)
        elif name not in registered:
            ghosts.append(name)
    missing = sorted(indexed - remote_names)
    return RemoteComparison(ghosts=ghosts, missing=missing, unknown_namespace=unknown)


def _namespace_of(name: str, namespaces: dict[str, str]) -> str | None:
    """The tenant namespace ``name`` lives in, or ``None`` when there is none.

    No tie-break is needed and none is written: a namespace ends with the
    separator (``Tenant.document_namespace`` -> ``"acme/"``), so two of them can
    never both prefix the same name — ``"acme/"`` does not match ``"acme-eu/x"``.
    That property is what keeps a tenant whose slug is a prefix of another's
    from stealing its documents, and it is pinned by a test on the property
    itself rather than by defensive code here.
    """
    for prefix, slug in namespaces.items():
        if name.startswith(prefix):
            return slug
    return None


def render_report(report: ReconcileReport) -> str:
    """Render the report as plain text (no Rich markup: it goes to logs/cron)."""
    rules = report.rules
    lines: list[str] = ["registry reconciliation report (read-only)"]
    if report.configuration_problem:
        # First, and labelled as configuration: this is not "OpenRAG is down".
        lines.append(
            f"! configuration problem, not a remote failure: "
            f"{report.configuration_problem}"
        )
        lines.append(
            "  (no tenant's remote side could be read; the local half below is"
            " unaffected)"
        )
    lines.extend(
        [
            f"  thresholds: indexing > {_minutes(rules.indexing_seconds)}"
            f" | deleting > {_minutes(rules.deleting_seconds)}",
            "",
        ]
    )
    lines.append(f"unsettled rows: {len(report.findings)}")
    for finding in report.findings:
        flag = " | remote outcome unknown" if finding.remote_outcome_unknown else ""
        # One field per line, and the name on a line of its own: names are
        # user-supplied and can be long enough to wrap, and a wrapped name used to
        # push "age=… status=…" onto a merged continuation line.
        lines.append(f"  [{finding.category}] {finding.stored_filename}")
        lines.append(
            f"      tenant {finding.tenant_slug} | age {_age(finding.age_seconds)}"
            f" | status {finding.status.value}{flag}"
        )
        lines.append(f"      next: {finding.action}")
        if finding.reason:
            lines.append(f"      why:  {finding.reason}")
    lines.append("")
    lines.append(f"ghosts (remote, unregistered): {len(report.remote.ghosts)}")
    lines.extend(f"  {name}" for name in report.remote.ghosts)
    lines.append(f"missing (registered, not remote): {len(report.remote.missing)}")
    lines.extend(f"  {name}" for name in report.remote.missing)
    lines.append(
        f"outside every tenant namespace: {len(report.remote.unknown_namespace)}"
    )
    lines.extend(f"  {name}" for name in report.remote.unknown_namespace)
    lines.append(f"tenants whose remote side could not be read: {len(report.remote_errors)}")
    for failure in report.remote_errors:
        lines.append(f"  {failure.tenant_slug}: {failure.detail}")
    if report.remote_errors:
        lines.append(
            "      (those tenants are excluded from the comparison above — an"
            " unreadable remote is not an empty one)"
        )
    lines.append("")
    lines.append(
        "nothing needs attention"
        if not report.needs_attention
        else "repairs are a later stage: this command never writes"
    )
    return "\n".join(lines)


def _minutes(seconds: float) -> str:
    return f"{seconds / 60:.0f}m"


def _age(seconds: float) -> str:
    """Age in units a person reads, coarsening as it grows.

    Hours stop being readable surprisingly fast — a 400-day-old row printed as
    ``9600.0h`` (the first version) is a number, not an age. Minutes, then hours,
    then days; triage does not need more precision than that.
    """
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


class ReconcileService:
    """Gather the two sides (registry rows, remote listing) and compare them."""

    def __init__(self, session: Any, gateway: Any, *, rules: StalenessRules) -> None:
        self._session = session
        self._gateway = gateway
        self._rules = rules

    async def _list_remote(self, api_key: str) -> list[str]:
        """Enumerate the remote side on a worker thread (the client is blocking).

        A named method rather than an inline lambda: the closure is what mypy
        needs to check the call, and this is the one line in the report that
        touches the network.
        """
        return await to_thread.run_sync(
            lambda: self._gateway.list_document_filenames(api_key=api_key)
        )

    async def run(self, *, tenant_slugs: list[str] | None = None) -> ReconcileReport:
        """Build the report. Reads only — no row is written, no remote call mutates.

        ``tenant_slugs`` limits the walk (one tenant, or a few); the default is
        every tenant, because a stuck row is not something a caller should have
        to know to ask about.

        A slug that matches nothing **raises** instead of producing an empty
        report. An empty report and a healthy one look identical — "nothing needs
        attention" with exit code 0 — so a typo would arrive as a green light,
        and this report is meant to be run from cron. There is nothing to report
        *about* when the filter matches nothing, so the request is refused.
        """
        from openrag_lab.infrastructure.db.repositories.identity import (
            SqlDocumentRepository,
            SqlTenantRepository,
        )
        from openrag_lab.infrastructure.openrag.tenant_scope import resolve_tenant_scope

        tenants = await SqlTenantRepository(self._session).list_all()
        if tenant_slugs is not None:
            wanted = set(tenant_slugs)
            tenants = [tenant for tenant in tenants if tenant.slug in wanted]
            unknown = sorted(wanted - {tenant.slug for tenant in tenants})
            if unknown or not tenants:
                # Both cases mean "there is nothing here to report on", which must
                # not share the signal of "everything is fine".
                raise NotFoundError(
                    "No tenant matched the filter: "
                    f"{', '.join(unknown) if unknown else '(empty filter)'}"
                )
        # document_namespace already ends with the separator ("acme/"); adding
        # another one here would match nothing and report every remote document
        # as living outside every namespace.
        namespaces = {tenant.document_namespace: tenant.slug for tenant in tenants}
        documents = SqlDocumentRepository(self._session)

        now = datetime.now(UTC)
        findings: list[Finding] = []
        # Only tenants whose remote listing was actually read take part in the
        # comparison: a failure to read is not evidence of absence, and mixing
        # the two would report every healthy document of an unreachable tenant
        # as "missing".
        registered: set[str] = set()
        indexed: set[str] = set()
        remote: list[str] = []
        remote_errors: list[RemoteUnavailable] = []
        configuration_problem: str | None = None
        for tenant in tenants:
            rows = await documents.list_unsettled(tenant.id)
            findings.extend(
                classify_registry(rows, tenant_slug=tenant.slug, now=now, rules=self._rules)
            )
            tenant_registered = set(await documents.list_all_stored_filenames(tenant.id))
            tenant_indexed = set(await documents.list_stored_filenames(tenant.id))
            try:
                # Resolving the key is part of "reading the remote side": a
                # missing key is exactly the kind of thing this report should
                # say out loud instead of dying on.
                tenant_remote = await self._list_remote(
                    resolve_tenant_scope(tenant).api_key
                )
            except ConfigurationError as exc:
                # A deployment error, in the words of resolve_tenant_scope — and
                # it fails identically for every tenant, so the walk stops here
                # rather than printing the same line once per tenant.
                logger.warning("Reconciliation cannot read the remote side: %s", exc)
                configuration_problem = str(exc)
                break
            except Exception as exc:  # noqa: BLE001 - the report must survive this
                logger.warning("Remote listing failed for tenant %s: %s", tenant.slug, exc)
                remote_errors.append(
                    RemoteUnavailable(
                        tenant_slug=tenant.slug,
                        detail=f"{type(exc).__name__}: {exc}",
                    )
                )
                continue
            registered |= tenant_registered
            indexed |= tenant_indexed
            remote.extend(tenant_remote)
        findings.sort(key=lambda finding: finding.age_seconds, reverse=True)
        return ReconcileReport(
            findings=findings,
            remote=compare_remote(
                registered=registered,
                indexed=indexed,
                remote=remote,
                namespaces=namespaces,
            ),
            rules=self._rules,
            remote_errors=remote_errors,
            configuration_problem=configuration_problem,
        )
