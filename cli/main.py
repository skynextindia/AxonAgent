from typing import Optional
import datetime
import typer
import questionary
from pathlib import Path
from functools import wraps
from rich.console import Console
from rich.panel import Panel
from rich.spinner import Spinner
from rich.live import Live
from rich.columns import Columns
from rich.markdown import Markdown
from rich.layout import Layout
from rich.text import Text
from rich.table import Table
from collections import deque
import time
from rich.tree import Tree
from rich import box
from rich.align import Align
from rich.rule import Rule

from axonai.default_config import DEFAULT_CONFIG
from cli.models import AnalystType
from cli.utils import *

console = Console()

app = typer.Typer(
    name="AxonAI",
    help="AxonAI CLI: Pure-Math Real-Time Trading Framework",
    add_completion=True,  # Enable shell completion
)

@app.command()
def live(
    ticker: str = typer.Option("EURUSD=X", "-t", "--ticker"),
    cooldown: int = typer.Option(300, "--cooldown", help="Seconds between entry signals"),
    host: str = typer.Option("127.0.0.1", "--host", help="Dashboard server host"),
    port: int = typer.Option(8000, "--port", help="Dashboard server port"),
    non_interactive: bool = typer.Option(False, "-y", "--non-interactive"),
):
    """Start the real-time event-driven trading daemon."""
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler()]
    )

    from axonai.realtime.daemon import AxonDaemon
    from axonai.realtime.api_server import start_dashboard

    config = DEFAULT_CONFIG.copy()

    config["realtime_cooldown_seconds"] = cooldown

    console.print(f"[bold green]Starting Web GUI Dashboard on http://{host}:{port}/[/bold green]")
    start_dashboard(host=host, port=port)
    
    daemon = AxonDaemon(symbol=ticker, config=config)
    
    try:
        console.print("[bold green]AxonAI Daemon starting...[/bold green]")
        daemon.start()
    except KeyboardInterrupt:
        console.print("[bold yellow]Shutting down...[/bold yellow]")
        daemon.stop()
        
        # Dry Run Summary
        try:
            from axonai.realtime.daemon import generate_session_summary
            generate_session_summary()
        except ImportError:
            pass


@app.command()
def backtest(
    ticker: str = typer.Option("EURUSD=X", "-t", "--ticker", help="Ticker symbol to backtest"),
    days: int = typer.Option(5, "-d", "--days", help="Number of historical days to backtest"),
    non_interactive: bool = typer.Option(False, "-y", "--non-interactive", help="Run without prompts")
):
    """Run historical data backtest for candle, peak, reversal, and sweep detections and trigger mock trades."""
    console.print(f"[bold green]Starting Backtest for {ticker} over {days} days...[/bold green]")
    
    from axonai.realtime.backtester import BacktestEngine
    
    engine = BacktestEngine(ticker=ticker, days=days)
    report = engine.run()
    
    # Print a beautiful Rich summary table
    console.print()
    console.print(Panel(f"[bold cyan]Backtest Performance Results for {ticker}[/bold cyan]", border_style="cyan"))
    
    table = Table(show_header=True, header_style="bold magenta", box=box.DOUBLE)
    table.add_column("Metric", style="dim", width=25)
    table.add_column("Value", style="green", justify="right", width=20)
    
    table.add_row("Total Triggered Trades", str(report["total_trades"]))
    table.add_row("Wins", f"{report['wins']} OK")
    table.add_row("Losses", f"{report['losses']} XX")
    table.add_row("Win Rate", f"{report['win_rate_percent']}%")
    table.add_row("Net Profit (Pips)", f"{report['net_profit_pips']:+.1f} pips")
    table.add_row("Profit Factor", f"{report['profit_factor']}")

    try:
        console.print(table)
    except Exception as e:
        print(f"BACKTEST RESULTS:")
        print(f"  Total Trades: {report['total_trades']}")
        print(f"  Wins: {report['wins']}")
        print(f"  Losses: {report['losses']}")
        print(f"  Win Rate: {report['win_rate_percent']}%")
        print(f"  Net Profit: {report['net_profit_pips']:+.1f} pips")
        print(f"  Profit Factor: {report['profit_factor']}")
    console.print()
    
    # Save the markdown report to disk
    import datetime
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = Path.cwd() / "reports"
    report_dir.mkdir(exist_ok=True)
    report_file = report_dir / f"backtest_{ticker.replace('=', '_')}_{timestamp}.md"
    
    md_content = engine.generate_markdown_report(report)
    report_file.write_text(md_content, encoding="utf-8")
    
    console.print(f"[green]\u2713 Backtest complete! Detailed report saved to:[/green] [bold]{report_file.resolve()}[/bold]")

if __name__ == "__main__":
    app()
