import os
import json
import sys
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict
from queue import Queue
from threading import Lock
import dotenv
import argparse
from tqdm import tqdm

import langchain_core.exceptions
from langchain_openai import ChatOpenAI
from langchain.prompts import (
    ChatPromptTemplate,
    SystemMessagePromptTemplate,
    HumanMessagePromptTemplate,
)
from structure import Structure
from checkpoints import CheckpointStore
from content_filter import is_sensitive
from runtime import build_chat_openai_kwargs, raise_if_processing_failed

if os.path.exists('.env'):
    dotenv.load_dotenv()
template = open("template.txt", "r").read()
system = open("system.txt", "r").read()

def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=str, required=True, help="jsonline data file")
    parser.add_argument("--max_workers", type=int, default=1, help="Maximum number of parallel workers")
    parser.add_argument(
        "--checkpoint-dir",
        help="Directory for per-paper checkpoints (default: beside the input data)",
    )
    return parser.parse_args()

def process_single_item(chain, item: Dict, language: str) -> Dict:
    def check_github_code(content: str) -> Dict:
        """提取并验证 GitHub 链接"""
        code_info = {}

        # 1. 优先匹配 github.com/owner/repo 格式
        github_pattern = r"https?://github\.com/([a-zA-Z0-9-_]+)/([a-zA-Z0-9-_\.]+)"
        match = re.search(github_pattern, content)
        
        if match:
            owner, repo = match.groups()
            # 清理 repo 名称，去掉可能的 .git 后缀或末尾的标点
            repo = repo.rstrip(".git").rstrip(".,)")
            
            full_url = f"https://github.com/{owner}/{repo}"
            code_info["code_url"] = full_url
            
            # 尝试调用 GitHub API 获取信息
            github_token = os.environ.get("TOKEN_GITHUB")
            headers = {"Accept": "application/vnd.github.v3+json"}
            if github_token:
                headers["Authorization"] = f"token {github_token}"
            
            try:
                api_url = f"https://api.github.com/repos/{owner}/{repo}"
                resp = requests.get(api_url, headers=headers, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    code_info["code_stars"] = data.get("stargazers_count", 0)
                    code_info["code_last_update"] = data.get("pushed_at", "")[:10]
            except Exception:
                # API 调用失败不影响主流程
                pass
            return code_info

        # 2. 如果没有 github.com，尝试匹配 github.io
        github_io_pattern = r"https?://[a-zA-Z0-9-_]+\.github\.io(?:/[a-zA-Z0-9-_\.]+)*"
        match_io = re.search(github_io_pattern, content)
        
        if match_io:
            url = match_io.group(0)
            # 清理末尾标点
            url = url.rstrip(".,)")
            code_info["code_url"] = url
            # github.io 不进行 star 和 update 判断
                
        return code_info

    # 检查 summary 字段
    if is_sensitive(item.get("summary", "")):
        return None

    # 检测代码可用性
    code_info = check_github_code(item.get("summary", ""))
    if code_info:
        item.update(code_info)

    """处理单个数据项"""
    # Default structure with meaningful fallback values
    default_ai_fields = {
        "tldr": "Summary generation failed",
        "motivation": "Motivation analysis unavailable",
        "method": "Method extraction failed",
        "result": "Result analysis unavailable",
        "conclusion": "Conclusion extraction failed"
    }
    
    try:
        response: Structure = chain.invoke({
            "language": language,
            "content": item['summary']
        })
        if response is None:
            raise ValueError("Model returned no structured summary")
        item['AI'] = response.model_dump()
    except langchain_core.exceptions.OutputParserException as e:
        # 尝试从错误信息中提取 JSON 字符串并修复
        error_msg = str(e)
        partial_data = {}
        
        if "Function Structure arguments:" in error_msg:
            try:
                # 提取 JSON 字符串
                json_str = error_msg.split("Function Structure arguments:", 1)[1].strip().split('are not valid JSON')[0].strip()
                # 预处理 LaTeX 数学符号 - 使用四个反斜杠来确保正确转义
                json_str = json_str.replace('\\', '\\\\')
                # 尝试解析修复后的 JSON
                partial_data = json.loads(json_str)
            except Exception as json_e:
                print(f"Failed to parse JSON for {item.get('id', 'unknown')}: {json_e}", file=sys.stderr)
        
        try:
            item['AI'] = Structure.model_validate(partial_data).model_dump()
        except Exception as parse_error:
            raise RuntimeError(
                f"Invalid structured summary for {item.get('id', 'unknown')}"
            ) from parse_error
        print(f"Recovered structured summary for {item.get('id', 'unknown')}", file=sys.stderr)
    except Exception as e:
        print(f"Unexpected error for {item.get('id', 'unknown')}: {e}", file=sys.stderr)
        raise RuntimeError(f"AI request failed for {item.get('id', 'unknown')}") from e
    
    # Final validation to ensure all required fields exist
    for field in default_ai_fields.keys():
        if field not in item['AI']:
            item['AI'][field] = default_ai_fields[field]

    # 检查 AI 生成的所有字段
    for v in item.get("AI", {}).values():
        if is_sensitive(str(v)):
            return None
    return item

def process_all_items(
    data: List[Dict], model_name: str, language: str, max_workers: int,
    checkpoint_dir=None, on_result=None,
) -> List[Dict]:
    """Reuse completed papers and checkpoint each newly completed summary."""
    llm = ChatOpenAI(
        **build_chat_openai_kwargs(
            model_name=model_name,
            base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            api_key=os.environ.get("OPENAI_API_KEY", ""),
        )
    ).with_structured_output(Structure, method="function_calling")

    print('Connect to:', model_name, file=sys.stderr)
    prompt_template = ChatPromptTemplate.from_messages([
        SystemMessagePromptTemplate.from_template(system),
        HumanMessagePromptTemplate.from_template(template=template),
    ])
    chain = prompt_template | llm
    store = None
    if checkpoint_dir is not None:
        store = CheckpointStore(checkpoint_dir, {
            "model": model_name.strip(),
            "language": language,
            "base_url": os.environ.get(
                "OPENAI_BASE_URL", "https://api.openai.com/v1"
            ).strip().rstrip("/"),
            "system_prompt": system,
            "user_prompt": template,
            "schema": Structure.model_json_schema(),
        })

    processed_data = [None] * len(data)
    pending = []
    processing_errors = []
    for idx, item in enumerate(data):
        key = store.key(item) if store is not None else None
        cached = store.load(key) if store is not None else None
        if cached is not None:
            try:
                Structure.model_validate(cached.get("AI"))
                if cached.get("id") != item.get("id"):
                    raise ValueError("Checkpoint paper ID mismatch")
            except (ValueError, TypeError):
                print(f"Ignoring invalid summary checkpoint for {item.get('id')}", file=sys.stderr)
                cached = None
        if cached is not None:
            processed_data[idx] = cached
            if on_result is not None:
                on_result(cached)
        else:
            pending.append((idx, key))

    print(
        f"Resume: reused {len(data) - len(pending)} paper(s); "
        f"{len(pending)} paper(s) need AI processing.",
        file=sys.stderr,
    )
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_item = {
            executor.submit(process_single_item, chain, dict(data[idx]), language): (idx, key)
            for idx, key in pending
        }
        for future in tqdm(
            as_completed(future_to_item),
            total=len(pending),
            desc="Processing items",
        ):
            idx, key = future_to_item[future]
            try:
                result = future.result()
            except Exception as error:
                paper_id = data[idx].get("id", str(idx))
                print(f"Skipping failed paper {paper_id}: {error}", file=sys.stderr)
                processing_errors.append(f"{paper_id}: {error}")
                continue
            if result is not None:
                if store is not None:
                    store.save(key, result)
                processed_data[idx] = result
                if on_result is not None:
                    on_result(result)

    successful_data = [item for item in processed_data if item is not None]
    if processing_errors:
        print(
            f"Skipped {len(processing_errors)} failed paper(s); "
            f"keeping {len(successful_data)} processed paper(s).",
            file=sys.stderr,
        )
    raise_if_processing_failed(
        processing_errors, successful_count=len(successful_data)
    )
    return successful_data

def main():
    args = parse_args()
    model_name = os.environ.get("MODEL_NAME", "gpt-4o-mini")
    language = os.environ.get("LANGUAGE", 'Chinese')

    target_file = args.data.replace('.jsonl', f'_AI_enhanced_{language}.jsonl')
    progress_file = target_file + ".partial"
    checkpoint_dir = args.checkpoint_dir or str(
        Path(args.data).parent / ".ai-checkpoints"
    )

    # 读取数据
    data = []
    with open(args.data, "r") as f:
        for line in f:
            data.append(json.loads(line))

    # 去重
    seen_ids = set()
    unique_data = []
    for item in data:
        if item['id'] not in seen_ids:
            seen_ids.add(item['id'])
            unique_data.append(item)

    data = unique_data
    print('Open:', args.data, file=sys.stderr)
    
    # Flush every completed paper, including restored checkpoints.
    with open(progress_file, "w", encoding="utf-8") as progress:
        def save_progress(item):
            progress.write(json.dumps(item, ensure_ascii=False) + "\n")
            progress.flush()
            os.fsync(progress.fileno())

        processed_data = process_all_items(
            data, model_name, language, args.max_workers,
            checkpoint_dir=checkpoint_dir, on_result=save_progress,
        )

    # Publish a complete output in original input order only after batch success.
    with open(progress_file, "w", encoding="utf-8") as output:
        for item in processed_data:
            output.write(json.dumps(item, ensure_ascii=False) + "\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(progress_file, target_file)

if __name__ == "__main__":
    main()
