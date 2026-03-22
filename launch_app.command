#!/bin/zsh

cd "$(dirname "$0")" || exit 1

python3 start_local.py
status=$?

if [[ $status -ne 0 ]]; then
	echo
	echo "Launch failed with exit code $status."
	read "?Press Enter to close this window"
fi

exit $status