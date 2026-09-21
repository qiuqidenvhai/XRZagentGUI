#!/usr/bin/env python3
"""
诊断后端API端点，了解可用的接口
"""
import asyncio
import json
import aiohttp
from pathlib import Path

API = "http://127.0.0.1:8888"

async def test_endpoints():
    """测试各种可能的API端点"""
    endpoints = [
        "/", "/health", "/api/health", "/api/tasks", "/api/conversations",
        "/api/messages", "/api/platforms", "/events", "/shutdown",
        "/interrupt", "/attachments", "/files"
    ]

    async with aiohttp.ClientSession() as session:
        for ep in endpoints:
            try:
                async with session.get(API + ep, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                    status = resp.status
                    text = await resp.text()
                    print(f"\n=== {ep} (HTTP {status}) ===")
                    print(text[:500] if len(text) > 500 else text)
            except Exception as e:
                print(f"\n=== {ep} ERROR: {e} ===")

async def main():
    await test_endpoints()

if __name__ == "__main__":
    asyncio.run(main())
