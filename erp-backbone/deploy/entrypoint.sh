#!/bin/sh
set -eu
umask 077
mkdir -p "${ERP_MEDIA_ROOT:?}"
exec "$@"
