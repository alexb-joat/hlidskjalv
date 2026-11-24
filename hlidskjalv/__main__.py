import sys

from lunete.__main__ import main

print("lunete: `python -m hlidskjalv` is now `python -m lunete`; the old name works until 0.6", file=sys.stderr)
main()
