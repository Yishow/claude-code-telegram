#!/usr/bin/env bash
set -euo pipefail

UPSTREAM_REPO_DEFAULT="${UPSTREAM_REPO_DEFAULT:-https://github.com/RichardAtCT/claude-code-telegram.git}"
UPSTREAM_REMOTE="${UPSTREAM_REMOTE:-upstream}"
UPSTREAM_BRANCH="${UPSTREAM_BRANCH:-main}"
LOCAL_MAIN_BRANCH="${LOCAL_MAIN_BRANCH:-main}"
AUTO_YES="${AUTO_YES:-1}"
LAST_AUTO_STASH_REF=""
LAST_AUTO_STASH_HANDLED="0"

info() {
  printf '[INFO] %s\n' "$1"
}

warn() {
  printf '[WARN] %s\n' "$1"
}

die() {
  printf '[ERROR] %s\n' "$1" >&2
  exit 1
}

is_auto_yes() {
  case "$(printf '%s' "$AUTO_YES" | tr '[:upper:]' '[:lower:]')" in
    1|y|yes|true|on) return 0 ;;
    *) return 1 ;;
  esac
}

confirm_yes() {
  local prompt="$1"
  local answer

  if is_auto_yes; then
    info "AUTO_YES=1，已自動確認：${prompt}"
    return 0
  fi

  if [ ! -t 0 ]; then
    die "非互動模式下無法詢問。可改用 AUTO_YES=1 自動確認。"
  fi

  read -r -p "${prompt} [y/N]: " answer
  answer="$(printf '%s' "$answer" | tr '[:upper:]' '[:lower:]')"
  [ "$answer" = "y" ] || [ "$answer" = "yes" ]
}

require_git_repo() {
  git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "這裡不是 git repo。請先 cd 到專案目錄。"
}

current_branch() {
  local branch
  branch="$(git branch --show-current)"
  [ -n "$branch" ] || die "目前是 detached HEAD，請先切回分支再操作。"
  printf '%s' "$branch"
}

git_dir() {
  git rev-parse --git-dir
}

rebase_in_progress() {
  local gdir
  gdir="$(git_dir)"
  [ -d "$gdir/rebase-merge" ] || [ -d "$gdir/rebase-apply" ]
}

merge_in_progress() {
  local gdir
  gdir="$(git_dir)"
  [ -f "$gdir/MERGE_HEAD" ]
}

worktree_dirty() {
  ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git ls-files --others --exclude-standard)" ]
}

ensure_not_in_progress() {
  if rebase_in_progress; then
    die "偵測到 rebase 進行中。請先執行: make sync-continue 或 make sync-abort"
  fi
  if merge_in_progress; then
    die "偵測到 merge 進行中。請先解決或執行: git merge --abort"
  fi
}

ensure_clean_worktree() {
  if ! worktree_dirty; then
    return 0
  fi

  warn "工作樹不乾淨，這會阻止 rebase / 分支同步。"
  echo "目前變更："
  git status --short
  echo

  if ! confirm_yes "是否自動 stash（含未追蹤檔）後繼續？"; then
    die "已取消。請先整理工作樹後重試。"
  fi

  local stash_msg stash_ref
  stash_msg="fork-workflow:auto-stash $(date +%Y%m%d-%H%M%S)"
  git stash push --include-untracked -m "$stash_msg" >/dev/null
  stash_ref="stash@{0}"
  LAST_AUTO_STASH_REF="$stash_ref"
  LAST_AUTO_STASH_HANDLED="0"
  info "已自動暫存到 ${stash_ref}。"
  echo "你之後可用這個指令還原：git stash pop ${stash_ref}"

  if worktree_dirty; then
    die "自動 stash 後仍有未提交變更，請手動處理後再試。"
  fi
}

ensure_local_main_exists() {
  git show-ref --verify --quiet "refs/heads/${LOCAL_MAIN_BRANCH}" \
    || die "找不到本地分支 '${LOCAL_MAIN_BRANCH}'。請先建立它。"
}

ensure_main_tracks_origin() {
  if git remote get-url origin >/dev/null 2>&1; then
    git branch --set-upstream-to="origin/${LOCAL_MAIN_BRANCH}" "${LOCAL_MAIN_BRANCH}" >/dev/null 2>&1 || true
  fi
}

ensure_upstream_remote() {
  if git remote get-url "$UPSTREAM_REMOTE" >/dev/null 2>&1; then
    return 0
  fi
  info "找不到 remote '${UPSTREAM_REMOTE}'，自動新增: ${UPSTREAM_REPO_DEFAULT}"
  git remote add "$UPSTREAM_REMOTE" "$UPSTREAM_REPO_DEFAULT"
}

fetch_upstream() {
  ensure_upstream_remote
  info "抓取 ${UPSTREAM_REMOTE}/${UPSTREAM_BRANCH} 最新資訊..."
  if ! git fetch "$UPSTREAM_REMOTE"; then
    warn "抓取 ${UPSTREAM_REMOTE} 失敗。請檢查網路或 remote 設定後重試。"
    return 1
  fi
}

show_conflicts() {
  if git diff --name-only --diff-filter=U 2>/dev/null | grep -q .; then
    echo "衝突檔案："
    git diff --name-only --diff-filter=U | sed 's/^/  - /'
  fi
}

show_status() {
  require_git_repo
  ensure_upstream_remote
  if ! git fetch "$UPSTREAM_REMOTE" -q; then
    warn "抓取 ${UPSTREAM_REMOTE} 失敗，以下狀態可能不是最新。"
  fi

  local branch
  branch="$(current_branch)"

  echo "=== 當前狀態 ==="
  git status --short --branch
  echo
  echo "目前分支: ${branch}"
  echo "main 分支: ${LOCAL_MAIN_BRANCH}"
  echo "upstream: ${UPSTREAM_REMOTE}/${UPSTREAM_BRANCH}"
  echo
  echo "=== 你的提交（尚未進 upstream）==="
  git log --oneline --max-count=20 "${UPSTREAM_REMOTE}/${UPSTREAM_BRANCH}"..HEAD | \
    awk 'BEGIN{c=0} {c++; print "  "$0} END{if(c==0) print "  (none)"}'
  echo
  echo "=== upstream 尚未進你目前分支的提交 ==="
  git log --oneline --max-count=20 HEAD.."${UPSTREAM_REMOTE}/${UPSTREAM_BRANCH}" | \
    awk 'BEGIN{c=0} {c++; print "  "$0} END{if(c==0) print "  (none)"}'
}

sync_main() {
  require_git_repo
  ensure_not_in_progress
  ensure_clean_worktree
  ensure_local_main_exists
  if ! fetch_upstream; then
    return 1
  fi

  local start_branch
  start_branch="$(current_branch)"

  if [ "$start_branch" != "$LOCAL_MAIN_BRANCH" ]; then
    info "切換到 ${LOCAL_MAIN_BRANCH}..."
    git switch "$LOCAL_MAIN_BRANCH"
  fi

  info "用 fast-forward 同步 ${LOCAL_MAIN_BRANCH} <- ${UPSTREAM_REMOTE}/${UPSTREAM_BRANCH}"
  if ! git merge --ff-only "${UPSTREAM_REMOTE}/${UPSTREAM_BRANCH}"; then
    warn "無法 fast-forward。代表你的 ${LOCAL_MAIN_BRANCH} 可能有私有提交。"
    warn "建議先把自訂提交移到 feature/* 分支，再讓 main 回到純 upstream。"
    if [ "$start_branch" != "$LOCAL_MAIN_BRANCH" ]; then
      git switch "$start_branch"
    fi
    return 1
  fi

  if git remote get-url origin >/dev/null 2>&1; then
    info "推送同步後的 ${LOCAL_MAIN_BRANCH} 到 origin/${LOCAL_MAIN_BRANCH}..."
    if ! git push origin "$LOCAL_MAIN_BRANCH"; then
      warn "推送失敗。請先處理遠端分支差異後重試。"
      return 1
    fi
  else
    warn "找不到 remote 'origin'，略過 push。"
  fi

  if [ "$start_branch" != "$LOCAL_MAIN_BRANCH" ]; then
    git switch "$start_branch"
  fi

  ensure_main_tracks_origin
  info "main 已與 upstream 對齊。"
}

rebase_current_onto_main() {
  require_git_repo
  ensure_not_in_progress
  ensure_clean_worktree
  ensure_local_main_exists

  local branch
  branch="$(current_branch)"
  if [ "$branch" = "$LOCAL_MAIN_BRANCH" ]; then
    die "目前就在 ${LOCAL_MAIN_BRANCH}，不需要 rebase。"
  fi

  info "把 ${branch} rebase 到 ${LOCAL_MAIN_BRANCH}..."
  if git rebase "$LOCAL_MAIN_BRANCH"; then
    info "rebase 完成。請推送：git push --force-with-lease origin ${branch}"
  else
    warn "rebase 發生衝突，請手動解決後執行：make sync-continue"
    warn "若要放棄：make sync-abort"
    show_conflicts
    return 1
  fi
}

sync_all() {
  require_git_repo
  local branch
  branch="$(current_branch)"

  sync_main

  if [ "$branch" = "$LOCAL_MAIN_BRANCH" ]; then
    info "你目前在 ${LOCAL_MAIN_BRANCH}，同步完成。"
    return 0
  fi

  rebase_current_onto_main
}

repair_main() {
  require_git_repo
  ensure_not_in_progress
  ensure_clean_worktree
  ensure_local_main_exists
  if ! fetch_upstream; then
    return 1
  fi

  local start_branch
  start_branch="$(current_branch)"

  if [ "$start_branch" != "$LOCAL_MAIN_BRANCH" ]; then
    info "切換到 ${LOCAL_MAIN_BRANCH}..."
    git switch "$LOCAL_MAIN_BRANCH"
  fi

  local ahead_count
  ahead_count="$(git rev-list --count "${UPSTREAM_REMOTE}/${UPSTREAM_BRANCH}..${LOCAL_MAIN_BRANCH}")"
  if [ "${ahead_count}" -eq 0 ]; then
    info "${LOCAL_MAIN_BRANCH} 沒有私有提交，不需要修復。"
    if [ "$start_branch" != "$LOCAL_MAIN_BRANCH" ]; then
      git switch "$start_branch"
    fi
    return 0
  fi

  local requested_name ts backup_branch feature_branch
  requested_name="${1:-}"
  ts="$(date +%Y%m%d-%H%M%S)"
  backup_branch="backup/main-before-repair-${ts}"

  if [ -n "$requested_name" ]; then
    requested_name="$(normalize_feature_name "$requested_name")"
  else
    requested_name="migrated-main-${ts}"
  fi

  if [[ "$requested_name" == feature/* ]]; then
    feature_branch="$requested_name"
  else
    feature_branch="feature/${requested_name}"
  fi

  git show-ref --verify --quiet "refs/heads/${backup_branch}" && die "備份分支已存在: ${backup_branch}"
  git show-ref --verify --quiet "refs/heads/${feature_branch}" && die "功能分支已存在: ${feature_branch}"

  warn "偵測到 ${LOCAL_MAIN_BRANCH} 有 ${ahead_count} 個私有提交。"
  echo "以下提交會搬到 ${feature_branch}:"
  git log --oneline "${UPSTREAM_REMOTE}/${UPSTREAM_BRANCH}..${LOCAL_MAIN_BRANCH}" | sed 's/^/  - /'
  echo
  if ! confirm_yes "確認修復 main（會把 main 對齊 upstream/main）？"; then
    info "已取消修復。"
    if [ "$start_branch" != "$LOCAL_MAIN_BRANCH" ]; then
      git switch "$start_branch"
    fi
    return 0
  fi

  info "建立備份分支: ${backup_branch}"
  git branch "$backup_branch" "$LOCAL_MAIN_BRANCH"
  info "建立功能分支: ${feature_branch}"
  git branch "$feature_branch" "$LOCAL_MAIN_BRANCH"

  info "重設 ${LOCAL_MAIN_BRANCH} 到 ${UPSTREAM_REMOTE}/${UPSTREAM_BRANCH}（保留備份與功能分支）..."
  git switch --detach >/dev/null 2>&1
  git branch -f "$LOCAL_MAIN_BRANCH" "${UPSTREAM_REMOTE}/${UPSTREAM_BRANCH}"
  git switch "$LOCAL_MAIN_BRANCH"

  local push_ok=1
  if git remote get-url origin >/dev/null 2>&1; then
    info "推送修復後的 ${LOCAL_MAIN_BRANCH} 到 origin/${LOCAL_MAIN_BRANCH}（force-with-lease）..."
    if ! git push --force-with-lease origin "$LOCAL_MAIN_BRANCH"; then
      push_ok=0
      warn "推送 main 失敗（遠端可能已變更）。"
      warn "請先執行: git fetch origin"
      warn "再手動推送: git push --force-with-lease origin ${LOCAL_MAIN_BRANCH}"
    fi
  else
    warn "找不到 remote 'origin'，略過 push。"
  fi

  ensure_main_tracks_origin

  info "切換到功能分支：${feature_branch}"
  git switch "$feature_branch"

  if [ -n "$LAST_AUTO_STASH_REF" ]; then
    info "偵測到本次 auto-stash，正在功能分支自動還原：${LAST_AUTO_STASH_REF}"
    if git stash pop "$LAST_AUTO_STASH_REF"; then
      info "已在 ${feature_branch} 還原暫存。"
      LAST_AUTO_STASH_REF=""
      LAST_AUTO_STASH_HANDLED="1"
    else
      warn "還原暫存時有衝突，請在 ${feature_branch} 手動解決。"
      return 1
    fi
  fi

  info "修復完成。"
  echo "後續建議："
  if [ "$push_ok" -eq 0 ]; then
    echo "  1) 先修正 main 推送：git fetch origin && git push --force-with-lease origin ${LOCAL_MAIN_BRANCH}"
    echo "  2) 再推送功能分支：git push -u origin ${feature_branch}"
    echo "  3) 之後日常同步改用：make sync"
    return 1
  else
    echo "  1) 推送功能分支：git push -u origin ${feature_branch}"
    echo "  2) 之後日常同步改用：make sync"
  fi
}

normalize_feature_name() {
  local raw="$1"
  local out
  out="$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]' | sed -E 's/[[:space:]]+/-/g; s/[^a-z0-9._\/-]/-/g; s/-+/-/g; s#(^[-/]+|[-/]+$)##g')"
  printf '%s' "$out"
}

new_feature_branch() {
  require_git_repo
  ensure_not_in_progress
  ensure_clean_worktree

  local input_name branch_name current
  input_name="${1:-}"
  if [ -z "$input_name" ]; then
    read -r -p "請輸入功能名稱（例如 telegram-sync-fix）: " input_name
  fi

  input_name="$(normalize_feature_name "$input_name")"
  [ -n "$input_name" ] || die "分支名稱不能為空。"

  if [[ "$input_name" == feature/* ]]; then
    branch_name="$input_name"
  else
    branch_name="feature/${input_name}"
  fi

  sync_main

  current="$(current_branch)"
  if [ "$current" != "$LOCAL_MAIN_BRANCH" ]; then
    git switch "$LOCAL_MAIN_BRANCH"
  fi

  git show-ref --verify --quiet "refs/heads/${branch_name}" && die "分支已存在: ${branch_name}"

  git switch -c "$branch_name" "$LOCAL_MAIN_BRANCH"
  info "已建立並切換到 ${branch_name}"
  echo "下一步建議："
  echo "  1) 開始開發並提交：git add -A && git commit -m 'feat: ...'"
  echo "  2) 推送分支：git push -u origin ${branch_name}"
  echo "  3) 後續同步 upstream：make sync"
}

sync_continue() {
  require_git_repo
  if ! rebase_in_progress; then
    die "目前沒有進行中的 rebase。"
  fi

  if git diff --name-only --diff-filter=U 2>/dev/null | grep -q .; then
    warn "仍有未解衝突，請先處理："
    show_conflicts
    exit 1
  fi

  GIT_EDITOR=true git rebase --continue
  if rebase_in_progress; then
    warn "rebase 尚未完成，請繼續解衝突後再次執行 make sync-continue"
  else
    info "rebase 完成。請推送：git push --force-with-lease"
  fi
}

sync_abort() {
  require_git_repo
  if ! rebase_in_progress; then
    die "目前沒有進行中的 rebase。"
  fi
  git rebase --abort
  info "已取消 rebase，分支回到 rebase 前狀態。"
}

find_latest_auto_stash_ref() {
  git stash list | awk '/fork-workflow:auto-stash/ && ref=="" {sub(/:$/, "", $1); ref=$1} END{print ref}'
}

stash_pop_auto() {
  require_git_repo

  local stash_ref
  if [ -n "$LAST_AUTO_STASH_REF" ]; then
    stash_ref="$LAST_AUTO_STASH_REF"
  elif [ "$LAST_AUTO_STASH_HANDLED" = "1" ]; then
    info "本次流程的 auto-stash 已由 repair-main 自動還原，不需要再執行 stash-pop。"
    return 0
  else
    stash_ref="$(find_latest_auto_stash_ref)"
  fi
  if [ -z "$stash_ref" ]; then
    die "找不到自動暫存（fork-workflow:auto-stash）。"
  fi

  echo "找到最近的自動暫存：${stash_ref}"
  git stash list | awk -v ref="$stash_ref" '$1==ref":" {print "  "$0}'
  echo
  if ! confirm_yes "要還原這個暫存到目前分支嗎？"; then
    info "已取消。"
    return 0
  fi

  if git stash pop "$stash_ref"; then
    info "暫存已還原。"
    if [ "$stash_ref" = "$LAST_AUTO_STASH_REF" ]; then
      LAST_AUTO_STASH_REF=""
      LAST_AUTO_STASH_HANDLED="1"
    fi
  else
    warn "還原時有衝突，請依 Git 提示手動解決。"
    return 1
  fi
}

show_action_details() {
  case "${1}" in
    status)
      cat <<'EOF'
[狀態檢查]
用途:
  查看你目前分支和 upstream 的差異，確認是否需要同步。

會執行:
  - git fetch upstream
  - 顯示目前分支狀態
  - 列出「你多出的提交」與「upstream 多出的提交」

範例:
  make status
EOF
      ;;
    new-feature)
      cat <<'EOF'
[建立新功能分支]
用途:
  用正式 fork 流程開新功能，不把私有功能直接疊在 main。

會執行:
  1) 同步 main <- upstream/main（ff-only）
  2) 推送 main 到 origin/main
  3) 從 main 建立 feature/<name> 分支並切換

範例:
  make feature-new NAME=telegram-command-refactor
EOF
      ;;
    sync)
      cat <<'EOF'
[一鍵同步（推薦日常使用）]
用途:
  不記指令直接完成常見同步：先更新 main，再更新你目前功能分支。

會執行:
  1) 同步 main <- upstream/main（ff-only）
  2) 推送 main 到 origin/main
  3) 若你在 feature/*，自動 rebase 到 main

範例:
  make sync

注意:
  - 若有衝突，會停止並提示你用 make sync-continue / make sync-abort。
EOF
      ;;
    sync-main)
      cat <<'EOF'
[只同步 main]
用途:
  當你只想先把 main 對齊 upstream，而不動目前功能分支。

會執行:
  - git merge --ff-only upstream/main 到本地 main
  - git push origin main

範例:
  make sync-main
EOF
      ;;
    repair-main)
      cat <<'EOF'
[main 有私有提交修復]
用途:
  當 main 不是純 upstream（有你自己的提交）時，一鍵修復成正式 fork 結構。

會執行:
  1) 建立備份分支 backup/main-before-repair-<time>
  2) 建立功能分支 feature/migrated-main-<time>（或你指定的名稱）
  3) main 強制對齊 upstream/main
  4) push main 到 origin/main（force-with-lease）
  5) 自動切到新功能分支並還原本次 auto-stash（若有）

範例:
  make repair-main
  make repair-main NAME=my-main-work

注意:
  - 你的提交不會消失，會保留在 backup/* 與 feature/*。
  - 這個流程結束後通常不需要再手動按 10。
EOF
      ;;
    sync-branch)
      cat <<'EOF'
[只同步目前功能分支]
用途:
  只把目前功能分支 rebase 到本地 main。

會執行:
  - git rebase main

範例:
  make sync-branch
EOF
      ;;
    sync-continue)
      cat <<'EOF'
[繼續 rebase]
用途:
  解完衝突後，繼續進行中的 rebase。

範例:
  make sync-continue
EOF
      ;;
    sync-abort)
      cat <<'EOF'
[取消 rebase]
用途:
  放棄這次 rebase，回到開始前狀態。

範例:
  make sync-abort
EOF
      ;;
    stash-pop)
      cat <<'EOF'
[還原自動暫存]
用途:
  還原最近一次由懶人流程建立的 auto-stash。

會執行:
  - 找出最新的 fork-workflow:auto-stash
  - 執行 git stash pop <stash-ref>

範例:
  make stash-pop
EOF
      ;;
    best-practice)
      cat <<'EOF'
[正式 fork 工作流]
1) main 僅用來追 upstream，不放私有功能提交。
2) 新功能一律在 feature/* 開發。
3) 每次開工前跑 make sync。
4) rebase 後推送使用 git push --force-with-lease（不要用 --force）。
5) 若 main 已經混入私有提交，先跑 make repair-main 修復。

常見節奏:
  make feature-new NAME=my-feature
  # 開發 + commit
  make sync
  git push --force-with-lease origin feature/my-feature
EOF
      ;;
    *)
      die "未知選項說明: ${1}"
      ;;
  esac
}

confirm_run() {
  local action="$1"
  shift

  show_action_details "$action"
  echo
  if ! confirm_yes "要執行這個動作嗎？"; then
    info "已取消。"
    return 0
  fi

  if ! ( "$@" ); then
    warn "動作失敗，請依提示修正後重試。"
  fi
}

show_menu() {
  cat <<'EOF'
============================================
 Fork 工作流懶人選單（不需要記 Git 指令）
============================================
 建議工作流範例：
   初次開工：2 -> 10 -> 開發/commit
   日常同步：3 -> (有 auto-stash 就再 10)
   main 有私有提交修復：9（自動切到 feature 並還原）

1) 查看同步狀態
2) 建立新功能分支（feature/*）
3) 一鍵同步（main + 目前分支）
4) 只同步 main（upstream -> main）
5) 只同步目前分支（rebase 到 main）
6) 繼續 rebase（解衝突後）
7) 取消 rebase
8) 顯示正式做法與範例
9) main 有私有提交修復
10) 還原自動暫存（stash pop）
0) 離開
EOF
}

run_menu() {
  while true; do
    echo
    show_menu
    echo
    read -r -p "請輸入選項 [0-10]: " choice

    case "$choice" in
      1) confirm_run status show_status ;;
      2) confirm_run new-feature new_feature_branch ;;
      3) confirm_run sync sync_all ;;
      4) confirm_run sync-main sync_main ;;
      5) confirm_run sync-branch rebase_current_onto_main ;;
      6) confirm_run sync-continue sync_continue ;;
      7) confirm_run sync-abort sync_abort ;;
      8) show_action_details best-practice ;;
      9)
        show_action_details repair-main
        echo
        if ! ( repair_main ); then
          warn "動作失敗，請依提示修正後重試。"
        fi
        ;;
      10) confirm_run stash-pop stash_pop_auto ;;
      0) info "已退出。"; return 0 ;;
      *) warn "無效選項，請輸入 0-10。" ;;
    esac
  done
}

usage() {
  cat <<'EOF'
Usage:
  scripts/fork_workflow.sh menu
  scripts/fork_workflow.sh status
  scripts/fork_workflow.sh new-feature [name]
  scripts/fork_workflow.sh sync
  scripts/fork_workflow.sh sync-main
  scripts/fork_workflow.sh repair-main [feature-name]
  scripts/fork_workflow.sh sync-branch
  scripts/fork_workflow.sh sync-continue
  scripts/fork_workflow.sh sync-abort
  scripts/fork_workflow.sh stash-pop
  scripts/fork_workflow.sh best-practice
EOF
}

main() {
  local cmd="${1:-menu}"
  case "$cmd" in
    menu) run_menu ;;
    status) show_status ;;
    new-feature) shift; new_feature_branch "${1:-}" ;;
    sync) sync_all ;;
    sync-main) sync_main ;;
    repair-main) shift; repair_main "${1:-}" ;;
    sync-branch) rebase_current_onto_main ;;
    sync-continue) sync_continue ;;
    sync-abort) sync_abort ;;
    stash-pop) stash_pop_auto ;;
    best-practice) show_action_details best-practice ;;
    help|-h|--help) usage ;;
    *) usage; die "未知指令: ${cmd}" ;;
  esac
}

main "$@"
