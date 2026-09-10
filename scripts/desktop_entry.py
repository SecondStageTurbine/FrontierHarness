"""PyInstaller entrypoint; no repository or installed Python is needed at runtime."""
from multiprocessing import freeze_support

if __name__=='__main__':
    freeze_support()
    from backend.desktop import main
    main()
