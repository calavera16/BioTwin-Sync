"""Source/frozen entrypoint; owned by the Electron parent through stdio."""
from biotwin.browser_host import main

if __name__ == '__main__':
    raise SystemExit(main())
