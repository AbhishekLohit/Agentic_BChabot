import requests
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.rule import Rule
from rich.prompt import Prompt
from rich import box
from rich.padding import Padding
from rich.live import Live
from rich.spinner import Spinner
from rich.align import Align

console = Console()

URL = "http://127.0.0.1:8000/process"
CUSTOMER_ID = "Cust_0410"
SESSION_ID = "2"


def print_banner():
    banner = Text()
    banner.append("  Banking Transaction Assistant  ", style="bold white on dark_blue")
    console.print()
    console.print(Align.center(banner))
    console.print(
        Align.center(
            Text("Powered by LangGraph + GPT-4.1-mini", style="dim italic")
        )
    )
    console.print()
    info = (
        f"[bold cyan]Customer:[/bold cyan] [white]{CUSTOMER_ID}[/white]   "
        f"[bold cyan]Session:[/bold cyan] [white]{SESSION_ID}[/white]   "
        f"[bold cyan]Endpoint:[/bold cyan] [white]{URL}[/white]"
    )
    console.print(Panel(info, box=box.ROUNDED, border_style="cyan", padding=(0, 2)))
    console.print(
        Padding(
            "[dim]Type your question and press Enter. Type [bold]exit[/bold] or [bold]quit[/bold] to stop.[/dim]",
            (1, 2),
        )
    )
    console.print(Rule(style="dim"))


def print_user_message(message: str):
    ts = datetime.now().strftime("%H:%M:%S")
    content = Text()
    content.append(f" {message} ", style="bold white")
    console.print()
    console.print(
        Align.right(
            Panel(
                content,
                title=f"[dim]{ts}[/dim]",
                title_align="right",
                border_style="blue",
                box=box.ROUNDED,
                padding=(0, 2),
                width=min(len(message) + 10, console.width - 6),
            )
        )
    )


def print_bot_message(message: str):
    ts = datetime.now().strftime("%H:%M:%S")
    console.print(
        Panel(
            Text(message, style="white"),
            title=f"[bold green] Assistant[/bold green]  [dim]{ts}[/dim]",
            title_align="left",
            border_style="green",
            box=box.ROUNDED,
            padding=(0, 2),
        )
    )


def print_error(message: str):
    console.print(
        Panel(
            Text(str(message), style="red"),
            title="[bold red] Error[/bold red]",
            title_align="left",
            border_style="red",
            box=box.ROUNDED,
            padding=(0, 2),
        )
    )


def chat_with_bot():
    print_banner()

    while True:
        try:
            user_message = Prompt.ask("\n[bold blue]You[/bold blue]").strip()
        except (KeyboardInterrupt, EOFError):
            console.print()
            console.print(Rule(style="dim"))
            console.print(Align.center(Text("Session ended. Goodbye!", style="bold cyan")))
            console.print()
            break

        if not user_message:
            continue

        if user_message.lower() in {"exit", "quit"}:
            console.print()
            console.print(Rule(style="dim"))
            console.print(Align.center(Text("Session ended. Goodbye!", style="bold cyan")))
            console.print()
            break

        print_user_message(user_message)

        payload = {
            "customer_id": CUSTOMER_ID,
            "session_id": SESSION_ID,
            "user_query": user_message,
        }

        try:
            with Live(
                Padding(Spinner("dots", text=Text(" Thinking...", style="dim italic")), (0, 2)),
                console=console,
                refresh_per_second=12,
                transient=True,
            ):
                response = requests.post(URL, json=payload, timeout=60)
                response.raise_for_status()

            bot_reply = response.json()
            if isinstance(bot_reply, dict) and "error" in bot_reply:
                print_error(f"{bot_reply.get('error')}: {bot_reply.get('details', '')}")
            else:
                print_bot_message(str(bot_reply))

        except requests.exceptions.ConnectionError:
            print_error("Could not connect to the server. Make sure the FastAPI app is running.")
        except requests.exceptions.Timeout:
            print_error("The request timed out after 60 seconds.")
        except requests.exceptions.HTTPError as e:
            print_error(f"HTTP {response.status_code}: {e}")
        except Exception as e:
            print_error(str(e))


if __name__ == "__main__":
    chat_with_bot()
