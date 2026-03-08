"""Typer CLI entry point."""

import typer

from order_guard import __version__

app = typer.Typer(name="order-guard", help="OrderGuard — 企业数据智能监控中台")


def version_callback(value: bool):
    if value:
        typer.echo(f"order-guard {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-v", callback=version_callback, is_eager=True,
        help="显示版本号",
    ),
):
    """OrderGuard CLI"""


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="监听地址"),
    port: int = typer.Option(8000, help="监听端口"),
):
    """启动 FastAPI 服务"""
    import uvicorn

    uvicorn.run("order_guard.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    app()
