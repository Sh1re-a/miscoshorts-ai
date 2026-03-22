#!/bin/zsh

cd "$(dirname "$0")" || exit 1

if [[ -f "frontend/dist/index.html" ]]; then
	python3 -m app.app_launcher
else
	python3 -m app.start_local
fi
status=$?

if [[ $status -ne 0 ]]; then
	echo
	echo "Launch failed with exit code $status."
	read "?Press Enter to close this window"
fi

exit $status