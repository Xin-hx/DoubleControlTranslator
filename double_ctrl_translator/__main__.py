"""One entry point for source runs and the windowed executable."""
import sys


def main():
    if "--install-metadata" in sys.argv:
        sys.argv.remove("--install-metadata")
        from .deployment import main as run
        try:
            return run()
        except Exception:
            import traceback
            from .settings import data_dir
            directory = data_dir()
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "install-error.log").write_text(traceback.format_exc(), "utf-8")
            return 1
    if "--worker" in sys.argv or "--model-guard" in sys.argv:
        from .launch import pipe_stdio
        pipe_stdio()
        if "--worker" in sys.argv:
            sys.argv.remove("--worker")
            from .worker import main as run
        else:
            sys.argv.remove("--model-guard")
            from .model_guard import main as run
        return run()
    from .app import main as run
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
