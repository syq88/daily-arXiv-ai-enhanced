#!/usr/bin/env bash
set -euo pipefail

# main holds code; data holds the published history needed BEFORE deduplication.
archive=$(mktemp "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/arxiv-history.XXXXXX")
trap 'rm -f "$archive"' EXIT
if git ls-remote --exit-code --heads origin refs/heads/data > /dev/null; then
    git fetch --no-tags origin refs/heads/data:refs/remotes/origin/data
    git archive --format=tar refs/remotes/origin/data data > "$archive"
    tar -xf "$archive"
    echo "已恢复 data 分支历史数据 / Restored published history from data branch"
else
    status=$?
    if [ "$status" -eq 2 ]; then
        echo "data 分支尚不存在，首次运行 / No data branch yet; first run"
    else
        echo "无法读取 data 分支，停止运行以避免重复处理 / Cannot read data branch; refusing to process without history" >&2
        exit "$status"
    fi
fi
