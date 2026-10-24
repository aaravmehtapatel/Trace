"""Portfolio census CLI: init, sync, review-queue, score, export."""
import asyncio
import json
from datetime import date

import click
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn
from rich.table import Table

from src import config as config_mod
from src import db, pipeline, score as score_mod

console = Console()
BUCKET_ORDER = ["Active", "Fading", "Quiet", "No Public Activity", "Unresolved"]
BUCKET_STYLE = {"Active": "cyan", "Fading": "magenta", "Quiet": "yellow", "No Public Activity": "red", "Unresolved": "dim"}


def label(bucket: str, last: str | None) -> str:
    if bucket == "No Public Activity":
        return f"No Public Activity Since {last}" if last else "No Public Activity (no dated signal)"
    return bucket


@click.group()
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False),
              default=str(config_mod.DEFAULT_CONFIG), show_default=True)
@click.pass_context
def cli(ctx: click.Context, config_path: str) -> None:
    try:
        ctx.obj = config_mod.load(config_mod.Path(config_path))
    except config_mod.ConfigError as e:
        raise click.ClickException(f"config: {e}")


@cli.command()
@click.option("--portfolio/--no-portfolio", default=True, help="Pull the fund's public portfolio table.")
@click.pass_obj
def init(cfg: config_mod.Config, portfolio: bool) -> None:
    """Create the database and load the portfolio."""
    conn = db.init(cfg)
    if portfolio:
        n = pipeline.load_portfolio(conn)
        console.print(f"Pulled {n} companies from the fund's portfolio page")
    n = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    console.print(f"[bold]{cfg.fund_name}[/]: {n} companies in {cfg.db_path.relative_to(config_mod.ROOT)}")


@cli.command()
@click.option("--skip-domains", is_flag=True, help="Don't look for websites of unresolved companies.")
@click.option("--only", type=click.Choice(pipeline.STAGES), default=None, help="Run a single stage.")
@click.pass_obj
def sync(cfg: config_mod.Config, skip_domains: bool, only: str | None) -> None:
    """Collect public signals for every company."""
    conn = db.init(cfg)
    with Progress(TextColumn("{task.description}"), BarColumn(), MofNCompleteColumn(), console=console) as bar:
        tasks: dict[str, int] = {}

        def progress(stage: str, done: int, total: int):
            if stage not in tasks:
                tasks[stage] = bar.add_task(stage, total=total)
            bar.update(tasks[stage], completed=done)

        asyncio.run(pipeline.sync(conn, cfg, progress, skip_domains=skip_domains, only=only))
    rows = conn.execute("SELECT state, COUNT(*) n FROM match_candidates GROUP BY state").fetchall()
    console.print("Match candidates: " + ", ".join(f"{r['state']} {r['n']}" for r in rows))


@cli.command("review-queue")
@click.option("--source", type=click.Choice(["sec_edgar", "domain"]), default=None)
@click.pass_obj
def review_queue(cfg: config_mod.Config, source: str | None) -> None:
    """Confirm or reject matches the scorer wasn't sure about."""
    conn = db.init(cfg)
    q = """SELECT m.*, e.raw_name, e.city, e.main_domain FROM match_candidates m JOIN entities e ON e.id = m.entity_id
           WHERE m.state = 'PENDING_REVIEW'""" + (" AND m.source = ?" if source else "") + " ORDER BY m.score DESC"
    pending = conn.execute(q, (source,) if source else ()).fetchall()
    if not pending:
        console.print("Nothing to review.")
        return
    f = None
    for i, m in enumerate(pending, 1):
        console.rule(f"{i}/{len(pending)}  ·  {m['source']}  ·  score {m['score']:.2f}")
        console.print(f"[bold]{m['raw_name']}[/] ({m['city'] or 'city unknown'}, {m['main_domain'] or 'no domain'})")
        console.print(f"   candidate: [bold]{m['candidate_name']}[/]  [{m['candidate_key']}]")
        console.print(f"   jaro-winkler {m['jaro_winkler'] or 0:.2f}  token-set {m['token_set'] or 0:.2f}  "
                      f"domain match {'yes' if m['domain_match'] else 'no'}")
        choice = click.prompt("   [y]es  [n]o  [s]kip  [q]uit", type=click.Choice(list("ynsq")), show_choices=False)
        if choice == "q":
            break
        if choice == "s":
            continue
        state = "CONFIRMED" if choice == "y" else "REJECTED"
        with conn:
            conn.execute("UPDATE match_candidates SET state = ?, reviewed_at = datetime('now') WHERE id = ?",
                         (state, m["id"]))
            if state == "CONFIRMED" and m["source"] == "domain":
                conn.execute("UPDATE entities SET main_domain = ? WHERE id = ?", (m["candidate_key"], m["entity_id"]))
                # One site per company: the other guesses for it are settled too.
                conn.execute("""UPDATE match_candidates SET state = 'REJECTED', reviewed_at = datetime('now')
                                WHERE entity_id = ? AND source = 'domain' AND id != ? AND state = 'PENDING_REVIEW'""",
                             (m["entity_id"], m["id"]))
    console.print("Saved. Run `sync --skip-domains` to pull signals for newly confirmed matches.")


@cli.command()
@click.pass_obj
def score(cfg: config_mod.Config) -> None:
    """Bucket every company and print the census."""
    conn = db.init(cfg)
    counts = score_mod.score_all(conn, cfg)
    total = sum(counts.values())
    t = Table(title=f"{cfg.fund_name} · portfolio census · {date.today():%B %Y}", title_justify="left")
    t.add_column("Bucket")
    t.add_column("Companies", justify="right")
    t.add_column("Share", justify="right")
    for b in BUCKET_ORDER:
        n = counts.get(b, 0)
        t.add_row(f"[{BUCKET_STYLE[b]}]{b}[/]", str(n), f"{n / total:.0%}" if total else "-")
    console.print(t)


@cli.command()
@click.option("--out", "out_dir", type=click.Path(file_okay=False), default="exports", show_default=True)
@click.pass_obj
def export(cfg: config_mod.Config, out_dir: str) -> None:
    """Write the audit log (JSON, Markdown) and the HTML report."""
    from src import report
    conn = db.init(cfg)
    data = report.collect(conn, cfg)
    paths = report.write_all(data, config_mod.ROOT / out_dir)
    for p in paths:
        console.print(f"wrote {p.relative_to(config_mod.ROOT)}")


if __name__ == "__main__":
    cli()
