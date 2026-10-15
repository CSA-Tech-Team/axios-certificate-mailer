"""Command line: enqueue, send, status, requeue, import-history."""
from __future__ import annotations

import argparse
import math
import mimetypes
import signal
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from .config import ConfigError, load_config
from .emailutil import normalize_email
from .ingest import IngestError, read_table, validate_records, write_rejects
from .lock import FileLock, LockHeld
from .logutil import setup_logging
from .sender import (EXIT_DB, EXIT_LOCKED, EXIT_USAGE, RealClock, Runner, dry_run, send_test)
from .smtpclient import SmtpTransport, classify
from .store import STATUSES, Store, TemplateMismatch
from .template import TemplateSyntaxError, missing_tags, tags_in


def _confirm(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin or not sys.stdin.isatty():
        return False
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _read_text(path: str, what: str) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise IngestError(f"{what} {path} is not valid UTF-8")
    except OSError as exc:
        raise IngestError(f"cannot read {what} {path}: {exc}")
    if not text.strip():
        raise IngestError(f"{what} {path} is empty")
    return text


def cmd_enqueue(args, cfg, log) -> int:
    subject = args.subject
    if not subject.strip():
        raise IngestError("--subject must not be empty")
    for path, opt in ((args.template, "--template"), (args.text_template, "--text-template")):
        if path and Path(path).suffix.lower() != ".hbs":
            raise IngestError(f"{opt} must be a Handlebars .hbs file, got {path}")
    html_text = _read_text(args.template, "template file")
    body = _read_text(args.text_template, "text template file") if args.text_template else ""

    banner, banner_type = None, None
    if args.banner:
        banner_type = mimetypes.guess_type(args.banner)[0]
        if banner_type not in ("image/png", "image/jpeg", "image/gif"):
            raise IngestError(f"--banner must be a .png, .jpg or .gif image, got {args.banner}")
        try:
            banner = Path(args.banner).read_bytes()
        except OSError as exc:
            raise IngestError(f"cannot read banner {args.banner}: {exc}")
        if len(banner) > 1_000_000:
            log.warning("banner is %d KB; large images slow the send and may be clipped by Gmail", len(banner) // 1024)
    if "cid:banner" in html_text and not banner:
        raise IngestError("the template references cid:banner but no --banner image was given")
    if banner and "cid:banner" not in html_text:
        log.warning("--banner was given but the template never references cid:banner; it will be attached, not shown")

    table = read_table(args.csv, cfg.email_column, cfg.delimiter)
    # template must match the columns before anything touches the queue
    texts = [subject, body, html_text]
    try:
        missing = missing_tags(texts, table.header)
    except TemplateSyntaxError as exc:
        raise IngestError(f"template error: {exc}")
    if missing:
        raise IngestError(
            "template tag(s) with no matching CSV column: " + ", ".join("{{%s}}" % t for t in missing)
        )
    n = len(table.records)
    if n > cfg.csv_threshold:
        if not _confirm(f"{args.csv} has {n} data rows (threshold {cfg.csv_threshold}). Enqueue anyway?", args.yes):
            raise IngestError(
                f"{n} rows exceeds the threshold of {cfg.csv_threshold}; "
                "confirm interactively or re-run with --yes if this is the right file"
            )
    required = set(tags_in(texts))
    header_cols = set(tags_in([subject]))
    accepted, rejects = validate_records(table, required, header_cols)

    rejects_path = None
    if rejects:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        rejects_path = cfg.rejects_dir / f"rejects-{args.campaign}-{stamp}.csv"
        write_rejects(rejects_path, table.header, rejects)
    if not accepted:
        raise IngestError(
            f"no valid rows: all {n} rows were rejected"
            + (f" (see {rejects_path})" if rejects_path else "")
        )

    store = Store(cfg.db_path)
    try:
        try:
            cid, how = store.get_or_create_campaign(
                args.campaign, subject, body, html_text, allow_change=False, banner=banner, banner_type=banner_type)
        except TemplateMismatch as exc:
            if not _confirm(f"{exc}. Replace the snapshot for all unsent rows?", args.allow_template_change):
                raise IngestError(f"{exc}. Re-run with --allow-template-change to replace it.")
            cid, how = store.get_or_create_campaign(
                args.campaign, subject, body, html_text, allow_change=True, banner=banner, banner_type=banner_type)
        result = store.enqueue(cid, [(a.email, a.data, a.line) for a in accepted], rejects=len(rejects))
    finally:
        store.close()
    existing = result["existing"]
    log.info("enqueue campaign=%s rows=%d new=%d already_queued=%d rejected=%d",
             args.campaign, n, result["new"], sum(existing.values()), len(rejects))
    print(f"Campaign {args.campaign!r} ({how}, id {cid})")
    print(f"  rows read:      {n}")
    print(f"  newly enqueued: {result['new']}")
    detail = ", ".join(f"{k}: {v}" for k, v in sorted(existing.items()))
    print(f"  already queued: {sum(existing.values())}" + (f" ({detail})" if detail else ""))
    print(f"  rejected:       {len(rejects)}" + (f" -> {rejects_path}" if rejects_path else ""))
    return 0


def _resolve_campaign(store: Store, name: str | None) -> int | None:
    if name is None:
        return None
    row = store.campaign_by_name(name)
    if row is None:
        raise IngestError(f"unknown campaign {name!r}")
    return row["id"]


def cmd_send(args, cfg, log) -> int:
    store = Store(cfg.db_path)
    try:
        cid = _resolve_campaign(store, args.campaign)
        if args.dry_run:
            total, errors, n_err, sample = dry_run(cfg, store, cid)
            print(f"dry run: {total} unsent recipients rendered, {n_err} render error(s); nothing sent")
            for e in errors:
                print("  ERROR", e)
            if sample:
                print("--- sample ---\n" + sample)
            return 0 if not n_err else EXIT_USAGE
        if args.test_to:
            if not cfg.smtp_password:
                raise IngestError("SMTP password not configured (set MAILQ_SMTP_PASSWORD)")
            try:
                print(send_test(cfg, store, SmtpTransport(cfg), cid, args.test_to))
            except ValueError as exc:
                raise IngestError(str(exc))
            except Exception as exc:
                kind, text = classify(exc)
                print(f"test send failed ({kind.value}): {text}", file=sys.stderr)
                return 1
            return 0
        try:
            lock = FileLock(cfg.lock_path)
            lock.acquire()
        except LockHeld as exc:
            print(f"error: {exc}. Only one send may run per queue.", file=sys.stderr)
            return EXIT_LOCKED
        clock = RealClock()

        def _stop(signum, frame):
            if clock.stop.is_set():
                raise KeyboardInterrupt
            log.warning("signal %s received: finishing the current step then stopping (repeat to force)", signum)
            clock.stop.set()

        for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
            if sig is not None:
                try:
                    signal.signal(sig, _stop)
                except ValueError:
                    pass
        try:
            runner = Runner(cfg, store, SmtpTransport(cfg), log, clock, campaign_id=cid, limit=args.limit)
            result = runner.run()
        except KeyboardInterrupt:
            print("forced stop; in-flight row was returned to the queue", file=sys.stderr)
            return 130
        finally:
            lock.release()
        print(f"{result.reason}\nsent this run: {result.sent}, dead: {result.dead}, retries scheduled: {result.retried}")
        return result.exit_code
    finally:
        store.close()


def cmd_status(args, cfg, log) -> int:
    store = Store(cfg.db_path)
    try:
        cid = _resolve_campaign(store, args.campaign)
        counts = store.counts(cid)
        tz = cfg.tz()
        now = datetime.now(tz)
        day = now.strftime("%Y-%m-%d")
        sent_today, blocked = store.daily_state(day)
        print(f"Queue status{' for ' + repr(args.campaign) if args.campaign else ''}")
        for s in STATUSES:
            print(f"  {s:<14} {counts[s]}")
        print(f"Today ({day}): {sent_today}/{cfg.daily_cap} sent" + ("  [provider quota hit]" if blocked else ""))
        nxt = store.earliest_retry(cid)
        if nxt is not None:
            print("Next retry due: " + datetime.fromtimestamp(nxt, tz).isoformat(timespec="seconds"))
        dups = store.possible_duplicates(cid)
        if dups:
            print(f"Rows flagged possible_duplicate (recovered after a crash): {dups}")
        for c in store.campaigns():
            if cid is not None and c["id"] != cid:
                continue
            if c["last_enqueue_rejects"] is not None:
                print(f"Campaign {c['name']!r}: last enqueue rejected {c['last_enqueue_rejects']} row(s)")
        remaining = counts["pending"] + counts["retry_pending"] + counts["sending"]
        if remaining:
            room = max(cfg.daily_cap - sent_today, 0)
            days = max(1, math.ceil(remaining / cfg.daily_cap)) if room == 0 else math.ceil(max(remaining - room, 0) / cfg.daily_cap) + 1
            minutes = remaining / cfg.rate_per_sec / 60
            print(f"ETA at configured pace: ~{minutes:.0f} min of sending over {days} day(s) (cap {cfg.daily_cap}/day)")
        dead = store.dead_list(cid)
        print(f"Dead-letter entries: {len(dead)}")
        for d in dead:
            print(f"  [{d['campaign']}] {d['email_norm']}  attempts={d['attempts']}  last error: {d['last_error']}")
        return 0
    finally:
        store.close()


def cmd_requeue(args, cfg, log) -> int:
    if not args.all_dead and not args.email:
        raise IngestError("give --email ADDRESS (repeatable) or --all-dead")
    emails = None
    if not args.all_dead:
        emails = []
        for e in args.email:
            norm, err = normalize_email(e)
            if err:
                raise IngestError(f"invalid address {e!r}: {err}")
            emails.append(norm)
    store = Store(cfg.db_path)
    try:
        cid = _resolve_campaign(store, args.campaign)
        what = "ALL dead rows" if emails is None else f"{len(emails)} address(es)"
        if not _confirm(f"Reset {what} in campaign {args.campaign!r} from dead to retry_pending?", args.yes):
            raise IngestError("not confirmed (this is a deliberate manual action; use --yes to confirm)")
        n = store.requeue(cid, emails)
        log.warning("REQUEUE campaign=%s reset %d dead row(s) by operator", args.campaign, n)
        print(f"requeued {n} row(s)")
        return 0
    finally:
        store.close()


def cmd_import_history(args, cfg, log) -> int:
    try:
        lines = Path(args.file).read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise IngestError(f"cannot read {args.file}: {exc}")
    emails, skipped = [], []
    for line in lines:
        if not line.strip():
            continue
        norm, err = normalize_email(line.split(",")[0] if "," in line and "@" in line.split(",")[0] else line)
        (emails if norm else skipped).append(norm or f"{line.strip()!r} ({err})")
    store = Store(cfg.db_path)
    try:
        cid = store.ensure_campaign_for_import(args.campaign)
        res = store.import_history(cid, dict.fromkeys(emails))
    finally:
        store.close()
    print(f"imported into {args.campaign!r}: {res['added']} added, {res['updated']} updated, "
          f"{res['already']} already sent; skipped {len(skipped)} unparseable line(s)")
    for s in skipped[:10]:
        print("  skipped", s)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mailqueue", description="Resumable bulk email sender")
    p.add_argument("--config", help="JSON config file (or MAILQ_CONFIG)")
    p.add_argument("--data-dir", help="directory for queue.db, logs and rejects (keep it outside OneDrive)")
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("enqueue", help="validate a CSV and add recipients to a campaign")
    e.add_argument("--campaign", required=True)
    e.add_argument("--csv", required=True)
    e.add_argument("--subject", required=True, help="subject line, may contain {{tags}}")
    e.add_argument("--template", required=True, metavar="FILE.hbs",
                   help="Handlebars email body (HTML); {{x}} is HTML-escaped, {{{x}}} is raw")
    e.add_argument("--text-template", metavar="FILE.hbs",
                   help="optional plain-text alternative (.hbs); derived from the HTML if omitted")
    e.add_argument("--banner", metavar="IMAGE",
                   help="banner image (png/jpg/gif) embedded in the email; reference it in the template as src=\"cid:banner\"")
    e.add_argument("--email-column")
    e.add_argument("--delimiter")
    e.add_argument("--yes", action="store_true", help="confirm a CSV above the row threshold")
    e.add_argument("--allow-template-change", action="store_true",
                   help="replace the campaign's template snapshot if it differs")
    e.set_defaults(fn=cmd_enqueue)

    s = sub.add_parser("send", help="send queued recipients")
    s.add_argument("--campaign")
    s.add_argument("--dry-run", action="store_true", help="render everything, send nothing")
    s.add_argument("--test-to", metavar="ADDRESS", help="send one sample to ADDRESS; queue untouched")
    s.add_argument("--limit", type=int, help="stop after this many successful sends")
    s.set_defaults(fn=cmd_send)

    st = sub.add_parser("status", help="per-state counts, quota use and dead letters")
    st.add_argument("--campaign")
    st.set_defaults(fn=cmd_status)

    r = sub.add_parser("requeue", help="EXPLICIT manual reset of dead rows to retry_pending")
    r.add_argument("--campaign", required=True)
    r.add_argument("--email", action="append")
    r.add_argument("--all-dead", action="store_true")
    r.add_argument("--yes", action="store_true")
    r.set_defaults(fn=cmd_requeue)

    h = sub.add_parser("import-history", help="mark previously emailed addresses as sent")
    h.add_argument("--campaign", required=True)
    h.add_argument("file", help="text/CSV file with one address per line (first column)")
    h.set_defaults(fn=cmd_import_history)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        overrides = {"data_dir": args.data_dir,
                     "email_column": getattr(args, "email_column", None),
                     "delimiter": getattr(args, "delimiter", None)}
        cfg = load_config(args.config, overrides)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        log = setup_logging(cfg)
        if "onedrive" in str(cfg.data_dir).lower():
            log.warning("data dir %s is inside OneDrive; SQLite files can corrupt or lock there", cfg.data_dir)
        return args.fn(args, cfg, log)
    except (ConfigError, IngestError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except (OSError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_DB


if __name__ == "__main__":
    sys.exit(main())
