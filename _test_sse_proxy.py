"""EventLogProxy 单元测试：模拟 pyc 里的 emit/裁剪路径，验证事件 id 化 + tail 语义。"""
import sys
sys.path.insert(0, r"D:/软件/XianRenZhangAgent")
import _sse_resilience as R

p = R.EventLogProxy()

# 模拟 pyc 的 _gui_emit_nowait：append 后判断 len>200 则裁掉最老 100 条
for i in range(260):
    p.append({"type": "t", "data": {"i": i}, "ts": i})
    if len(p) > 200:
        del p[:100]
    assert len(p) <= 200, f"裁剪后仍超: len={len(p)} @i={i}"

# 关键断言：裁剪前后 _id 单调、tail 语义正确
print("seq =", p.max_id())
assert p.max_id() == 259, "max_id 应保持全局单调 (0..259)"

# 模拟老客户端：连接时 last_id = 150（此时 log 里可能已被裁剪过）
last = 150
tail = p.tail(last)
ids = [e["_id"] for e in tail]
assert all(x > last for x in ids), f"tail 含未游标: {ids[:5]}"
assert ids == sorted(ids), "tail 应有序"
# 裁剪只删最老 100 条，_id>150 的事件必然还在（log 保留最后 ~100-200 条，150 在保留区）
assert 151 in ids, "151 应可见"
print("tail(150) 共", len(tail), "条, 首/尾 _id =", ids[0], ids[-1])

# 模拟刚连接的新客户端：last_id=-1 → 全量回放
full = p.tail(-1)
assert len(full) == len(p), f"全量回放 {len(full)} != log {len(p)}"
print("新客户端全量回放:", len(full), "条（=log 当前长度）OK")

# 边界：_id 比 log 中所有都大的游标 → 空增量
assert p.tail(99999) == []
print("过期游标边界 OK")

print("\n=== EventLogProxy 单元测试 4/4 通过 ===")
