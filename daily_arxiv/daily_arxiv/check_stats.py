#!/usr/bin/env python3
"""
检查Scrapy爬取统计信息的脚本 / Script to check Scrapy crawling statistics
用于获取去重检查的状态结果 / Used to get deduplication check status results

功能说明 / Features:
- 检查当日与已发布历史论文的重复情况 / Compare today with all published history
- 删除重复论文条目，保留新内容 / Remove duplicate papers, keep new content
- 根据去重后的结果决定工作流是否继续 / Decide workflow continuation based on deduplication results
"""
import json
import sys
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Beijing is UTC+08:00; use an explicit offset even outside the workflow.
BEIJING_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")


def get_run_date():
    """Use the date captured at run start, or today's date in Beijing."""
    configured_date = os.environ.get("ARXIV_RUN_DATE", "").strip()
    if configured_date:
        return date.fromisoformat(configured_date)
    return datetime.now(BEIJING_TIMEZONE).date()


def normalize_paper_id(value):
    """Treat arXiv URLs and version suffixes as the same paper."""
    value = str(value or "").strip()
    value = re.sub(r"^https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/", "", value)
    value = value.removesuffix(".pdf")
    return re.sub(r"v\d+$", "", value)


def load_published_history(data_dir, run_date):
    """Only successful earlier outputs count; failed raw papers remain retryable."""
    history_ids = set()
    for path in sorted(Path(data_dir).glob("*_AI_enhanced_*.jsonl")):
        match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})_AI_enhanced_.+\.jsonl", path.name)
        if match is None or date.fromisoformat(match[1]) >= run_date:
            continue
        _, ids = load_papers_data(path)
        history_ids.update(ids)
    return history_ids

def load_papers_data(file_path):
    """
    从jsonl文件中加载完整的论文数据
    Load complete paper data from jsonl file
    
    Args:
        file_path (str): JSONL文件路径 / JSONL file path
        
    Returns:
        list: 论文数据列表 / List of paper data
        set: 论文ID集合 / Set of paper IDs
    """
    if not os.path.exists(file_path):
        return [], set()
    
    papers = []
    ids = set()
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    data = json.loads(line)
                    papers.append(data)
                    paper_id = normalize_paper_id(data.get('id'))
                    if not paper_id:
                        raise ValueError(f"Missing paper ID in {file_path}")
                    ids.add(paper_id)
        return papers, ids
    except Exception as e:
        print(f"Error reading {file_path}: {e}", file=sys.stderr)
        raise

def save_papers_data(papers, file_path):
    """
    保存论文数据到jsonl文件
    Save paper data to jsonl file
    
    Args:
        papers (list): 论文数据列表 / List of paper data
        file_path (str): 文件路径 / File path
    """
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            for paper in papers:
                f.write(json.dumps(paper, ensure_ascii=False) + '\n')
        return True
    except Exception as e:
        print(f"Error saving {file_path}: {e}", file=sys.stderr)
        return False

def perform_deduplication():
    """
    执行多日去重：删除与历史多日重复的论文条目，保留新内容
    Perform deduplication over multiple past days
    
    Returns:
        str: 去重状态 / Deduplication status
             - "has_new_content": 有新内容 / Has new content
             - "no_new_content": 无新内容 / No new content  
             - "no_data": 无数据 / No data
             - "error": 处理错误 / Processing error
    """

    run_date = get_run_date()
    today = run_date.isoformat()
    today_file = f"../data/{today}.jsonl"

    if not os.path.exists(today_file):
        print("今日数据文件不存在 / Today's data file does not exist", file=sys.stderr)
        return "no_data"

    try:
        today_papers, _ = load_papers_data(today_file)
        print(f"今日论文总数: {len(today_papers)} / Today's total papers: {len(today_papers)}", file=sys.stderr)

        if not today_papers:
            return "no_data"

        history_ids = load_published_history("../data", run_date)
        print(f"已发布历史去重库大小: {len(history_ids)} / Published history deduplication library size: {len(history_ids)}", file=sys.stderr)

        seen_ids = set(history_ids)
        new_papers = []
        for paper in today_papers:
            paper_id = normalize_paper_id(paper.get('id'))
            if paper_id not in seen_ids:
                new_papers.append(paper)
                seen_ids.add(paper_id)
        duplicate_count = len(today_papers) - len(new_papers)

        if duplicate_count:
            print(f"移除 {duplicate_count} 条历史或当日重复记录 / Removed {duplicate_count} historical or same-day duplicate records", file=sys.stderr)

            print(f"去重后剩余论文数: {len(new_papers)} / Remaining papers after deduplication: {len(new_papers)}", file=sys.stderr)

            if new_papers:
                if save_papers_data(new_papers, today_file):
                    print(f"已更新今日文件，移除 {duplicate_count} 条重复记录 / Today's file updated, removed {duplicate_count} duplicate records", file=sys.stderr)
                    return "has_new_content"
                else:
                    print("保存去重后的数据失败 / Failed to save deduplicated data", file=sys.stderr)
                    return "error"
            else:
                try:
                    os.remove(today_file)
                    print("所有论文均为重复内容，已删除今日文件 / All papers are duplicate content, today's file deleted", file=sys.stderr)
                except Exception as e:
                    print(f"删除文件失败: {e} / Failed to delete file: {e}", file=sys.stderr)
                return "no_new_content"
        else:
            print("所有内容均为新内容 / All content is new", file=sys.stderr)
            return "has_new_content"

    except Exception as e:
        print(f"去重处理失败: {e} / Deduplication processing failed: {e}", file=sys.stderr)
        return "error"

def main():
    """
    检查去重状态并返回相应的退出码
    Check deduplication status and return corresponding exit code
    
    退出码含义 / Exit code meanings:
    0: 有新内容，继续处理 / Has new content, continue processing
    1: 无新内容，停止工作流 / No new content, stop workflow
    2: 处理错误 / Processing error
    """
    
    print("正在执行去重检查... / Performing intelligent deduplication check...", file=sys.stderr)
    
    # 执行去重处理 / Perform deduplication processing
    dedup_status = perform_deduplication()
    
    if dedup_status == "has_new_content":
        print("✅ 去重完成，发现新内容，继续工作流 / Deduplication completed, new content found, continue workflow", file=sys.stderr)
        sys.exit(0)
    elif dedup_status == "no_new_content":
        print("⏹️ 去重完成，无新内容，停止工作流 / Deduplication completed, no new content, stop workflow", file=sys.stderr)
        sys.exit(1)
    elif dedup_status == "no_data":
        print("⏹️ 今日无数据，停止工作流 / No data today, stop workflow", file=sys.stderr)
        sys.exit(1)
    elif dedup_status == "error":
        print("❌ 去重处理出错，停止工作流 / Deduplication processing error, stop workflow", file=sys.stderr)
        sys.exit(2)
    else:
        # 意外情况：未知状态 / Unexpected case: unknown status
        print("❌ 未知去重状态，停止工作流 / Unknown deduplication status, stop workflow", file=sys.stderr)
        sys.exit(2)

if __name__ == "__main__":
    main() 
