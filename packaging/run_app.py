"""PyInstaller entry point for GTA6StreamAI.exe."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from studio.launcher import main
    sys.exit(main())
