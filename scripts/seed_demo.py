"""一键生成演示数据：9年级21班 10 名学生 · 一学期 5 次课时作业 · 约 84 条错题。

对齐方案叙事曲线（按录入时间分周展示）：
  时态误用 B01        6→5→4→2→1   （重点讲 + 干预后显著下降）
  语序与成分 B08      3→3→2→1→1    （次重点，稳步下降）
  阅读推理越界 C03    3→2→2→2→1    （反复出现，进入个别辅导）
  其余 A/B/C/D 类零散分布，用于"不讲清单"演示。
教师改判：约 15% 留痕（AI 判 B06 教师改 E02 等——"护AI"核心数据）。

用法：
  python scripts/seed_demo.py            # 写入默认库 runtime/db/eta.db
  python scripts/seed_demo.py --reset    # 先清空再生成
  python scripts/seed_demo.py --db path  # 指定数据库文件
所有数据为脚本确定性生成，真实数据请由教师日常录入积累。
全程不调用任何大模型接口（离线可复现，与 seed_full 一致）。
"""
import argparse
import os
import random
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402

UNITS = ["Unit 1", "Unit 2", "Unit 3", "Unit 4", "Unit 5"]   # 唯一标识（batch_no）
EXAM_DATES = ["2026-03-13", "2026-03-27", "2026-04-17", "2026-05-08", "2026-05-29"]  # 录入日期（趋势分周）
CLASS_NAME = "9年级21班"
CLASS_SIZE = 10

STUDENT_NAMES = [
    "王一帆", "李知行", "张若虚", "陈静远", "刘一诺", "杨明轩", "赵清扬", "黄思齐",
    "周慕云", "吴亦然", "徐承宇", "孙艺洋", "胡以恒", "朱天佑", "高新远", "林致远",
    "何嘉言", "郭正则", "马千帆", "罗一鸣", "梁启航", "宋雨桐", "唐望舒", "许清如",
    "韩乐水", "冯牧野", "邓斯年", "曹知夏", "彭临溪", "曾繁星", "肖一苇", "田望岳",
    "董书言", "袁若谷", "潘云起", "蔡怀瑾", "蒋听澜", "余照野", "杜若飞", "叶知秋",
]

# 每类错题的题库模板（question, answer, correct, qtype）
BANK = {
    "B01": [
        ("He ____ (go) to the park yesterday.", "has gone", "went", "词语运用"),
        ("I ____ (see) the film two days ago.", "have seen", "saw", "词语运用"),
        ("Choose the correct sentence.", "She has visited Beijing last year.", "She visited Beijing last year.", "单选"),
        ("My father ____ (buy) the bike for me last month.", "has bought", "bought", "词语运用"),
    ],
    "B08": [
        ("I with my friends went swimming yesterday.", "I with my friends went swimming yesterday.", "I went swimming with my friends yesterday.", "单选"),
        ("Translation: 我不知道他为什么迟到。", "I don't know why is he late.", "I don't know why he is late.", "书面表达"),
    ],
    "C03": [
        ("What can we infer from the last paragraph?", "The writer will give up music forever.", "The writer may keep music as a hobby.", "阅读理解"),
        ("What can we learn from the passage?", "People must stop using phones at once.", "We should use phones in a proper way.", "阅读理解"),
        ("The writer mentions the numbers in Para. 2 to ____.", "show that everyone likes shopping online", "give facts to support his idea", "阅读理解"),
    ],
    "B06": [
        ("Choose the correct sentence.", "He like playing basketball after school.", "He likes playing basketball after school.", "单选"),
    ],
    "A05": [
        ("Choose the correct answer.", "She gave me two informations about the trip.", "She gave me two pieces of information about the trip.", "单选"),
    ],
    "B07": [
        ("Choose the correct answer.", "This book is more better than that one.", "This book is better than that one.", "单选"),
    ],
    "B03": [
        ("You'd better ____ too much junk food.", "to not eat", "not eat", "单选"),
    ],
    "B06b": [
        ("Translation: 山上有许多树。", "There have many trees on the hill.", "There are many trees on the hill.", "书面表达"),
    ],
    "D04": [
        ("Translation: 我非常喜欢英语。", "I very like English.", "I like English very much.", "书面表达"),
    ],
    "D05": [
        ("Choose the correct answer.", "Because he was ill, so he didn't come.", "Because he was ill, he didn't come.", "单选"),
    ],
    "C01": [
        ("What's the best title for the passage?", "A Sad Story About a Dog", "Never Give Up: A Boy and His Dog", "阅读理解"),
    ],
    "C02": [
        ("Which of the following is NOT true according to Para. 3?", "B. The school was built in 2010.", "D. The school has 2,000 students now.", "阅读理解"),
    ],
    "D08": [
        ("Fill in the blank: The story happened in ____ (a European country).", "an European country", "a European country", "词语运用"),
    ],
    "E02": [
        ("He ____ (be) to Shanghai twice.", "has been to Shanghai twice twice", "has been to Shanghai twice", "词语运用"),
    ],
}

# 叙事主线（类别 → 各批次人次；主线全部由教师采纳确认，曲线精确对齐方案）
STORYLINE = {
    "B01": [6, 5, 4, 2, 1],
    "B08": [3, 3, 2, 1, 1],
    "C03": [3, 2, 2, 2, 1],
}
# 零散分布（类别 → 各批次人次，供"不讲清单"演示）
SCATTERED = {
    "B06": [2, 1, 1, 1, 0],
    "A05": [1, 1, 1, 0, 0],
    "B07": [1, 1, 1, 0, 0],
    "B03": [1, 1, 0, 0, 0],
    "B06b": [1, 0, 1, 0, 0],
    "D04": [1, 1, 1, 0, 0],
    "D05": [1, 1, 1, 0, 0],
    "C01": [1, 1, 1, 1, 0],
    "C02": [1, 1, 0, 0, 0],
    "D08": [0, 1, 1, 0, 0],
    "E02": [1, 1, 1, 0, 0],
}

# 重点跟踪学生：同类错因跨批次聚集 → 演示“个别辅导名单”（同类≥3次进入）
# (学生, 类别, 各批次次数)；不参与改判，全部采纳
FOCUS_STUDENTS = [
    ("S07", "C03", [1, 1, 1, 1, 1]),   # 阅读推理反复错——建议面批推理过程
    ("S03", "B06", [0, 1, 2, 1, 1]),   # 三单在 5 次课时作业错了 4 次
    ("S09", "E02", [1, 1, 1, 1, 0]),   # 粗心型聚集（与 B01 边界案例）
]

# 教师改判对（AI 判 → 教师改判）：仅零散类别参与改判，主线曲线不受扰动；
# 体现教师把关 + 护AI 留痕（改判率约 15%）
OVERRIDE_MAP = {
    "B06": "E02",   # 表面三单错误，实为粗心漏 s
    "A05": "A01",   # 词汇类互改（不可数名词 → 拼写形近）
    "D04": "B08",   # 中式英语内部：直译语序 vs 逐字硬译
    "C01": "C03",   # 主旨题与推断题边界案例
    "D08": "A05",   # 冠词误用 vs 不可数名词混淆
    "E02": "B01",   # 行为类与知识类互改（粗心 vs 未掌握）
}
OVERRIDE_RATE = 0.65        # 零散类别中约 65% 被改判（AI 对低频类别最易判错）→ 总改判率约 15%

# 已审校补偿练习的确定性题组（演示用；真实练习由教师在页面向 AI 生成并审校）
_DEMO_PRACTICE_ITEMS = {
    "B01": [
        {"type": "单选", "q": "I ____ my homework already. What about you?",
         "options": ["finish", "finished", "have finished", "will finish"],
         "answer": "C", "explanation": "already 是现在完成时标志词"},
        {"type": "单选", "q": "He ____ to Beijing twice last year.",
         "options": ["has been", "went", "has gone", "goes"],
         "answer": "B", "explanation": "last year 是确定的过去时间，用一般过去时"},
        {"type": "改错", "q": "改正句子：I have seen the film yesterday.",
         "answer": "I saw the film yesterday.",
         "explanation": "yesterday 只能与一般过去时连用"},
    ],
    "B08": [
        {"type": "单选", "q": "Which sentence is correct English?",
         "options": ["I with my friends went swimming.", "I went swimming with my friends.",
                     "With my friends I went swimming.", "Went swimming I with my friends."],
         "answer": "B",
         "explanation": "with 短语作伴随状语须后置，不能置于主语与谓语之间"},
        {"type": "改错", "q": "改正句子：I by bus go to school every day.",
         "answer": "I go to school by bus every day.",
         "explanation": "方式状语 by bus 置于句末，不可插在主语和谓语之间"},
        {"type": "改错", "q": "改正句子：I don't know why is he late.",
         "answer": "I don't know why he is late.",
         "explanation": "宾语从句用陈述语序，不倒装"},
    ],
}


def reset(db_path: str) -> None:
    if os.path.exists(db_path):
        os.remove(db_path)
        print("已清空旧库：%s" % db_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成英语教学助手演示数据（离线确定性）")
    parser.add_argument("--db", default=None, help="数据库路径（默认 runtime/db/eta.db）")
    parser.add_argument("--reset", action="store_true", help="先清空旧库")
    args = parser.parse_args()

    if args.db:
        os.environ["ETA_DB"] = args.db
    from app import config
    db_path = config.get_db_path()
    if args.reset:
        reset(db_path)
    db.init_db()

    rng = random.Random(2026)   # 固定种子：任何人复现都得到同一份数据
    rng_ovr = random.Random(31)  # 改判专用独立随机流：调改判参数不影响叙事曲线

    # 1) 学生名单（姓名仅本地映射，出参永远只有代号）
    db.import_students([{"code": "S%02d" % (i + 1), "name": STUDENT_NAMES[i],
                         "class": CLASS_NAME}
                        for i in range(CLASS_SIZE)])

    # 2) 错题：叙事主线 + 零散
    plan = {}
    for cat, counts in list(STORYLINE.items()) + list(SCATTERED.items()):
        for ui, n in enumerate(counts):
            for k in range(n):
                plan.setdefault((ui, cat), []).append(k)
    # 重点跟踪学生的错题以 ("F", 代号) 标记，插入时直接指定学生
    for code, cat, per_unit in FOCUS_STUDENTS:
        for ui, n in enumerate(per_unit):
            for k in range(n):
                plan.setdefault((ui, cat), []).append(("F", code))

    codes = ["S%02d" % (i + 1) for i in range(CLASS_SIZE)]
    error_ids_by_unit = {i: [] for i in range(5)}
    inserted = 0

    for (ui, cat), ks in sorted(plan.items()):
        bank = BANK[cat]
        for j, item in enumerate(ks):
            q, a, c, qt = bank[j % len(bank)]
            # 确定性选人（普通错题）；重点跟踪学生直接指定
            fixed = item[1] if isinstance(item, tuple) else None
            offset = sum(ord(ch) for ch in cat) * 7 + j * 3
            student = fixed or codes[(ui * 5 + offset) % CLASS_SIZE]
            base = datetime.strptime(EXAM_DATES[ui], "%Y-%m-%d")
            created = base.replace(hour=15 + j % 5, minute=0).isoformat(timespec="seconds")
            eid = db.insert_error({
                "student_code": student, "class_name": CLASS_NAME,
                "question": q, "answer": a, "correct": c, "qtype": qt,
                "exam_type": "课时作业", "batch_no": UNITS[ui],
                "category_id": cat,
                "evidence": "演示数据：符合错因 %s 的典型特征" % cat,
                "teaching_point": "",
                "confidence": 0.88,
                "needs_review": 0,
                "created_at": created,
            })
            error_ids_by_unit[ui].append(eid)
            inserted += 1

            # 教师确认：主线类别全部采纳（曲线可控）；
            # 零散类别约 30% 被教师改判留痕（全部走 rng_ovr 独立流）
            if not fixed and cat in OVERRIDE_MAP and rng_ovr.random() < OVERRIDE_RATE:
                db.confirm_error(eid, "modify", override=OVERRIDE_MAP[cat])
            else:
                db.confirm_error(eid, "accept")

    # 3) 生成 2 套已审校补偿练习（决策单"配套练习"演示）
    #    确定性直插内置题组，不调用大模型（与 seed_full 一致，离线可复现）
    from app.services import practice as practice_svc
    for cid in ("B01", "B08"):
        pid = db.insert_practice(cid, _DEMO_PRACTICE_ITEMS[cid], class_name=CLASS_NAME)
        practice_svc.approve(pid)

    # 4) 汇报
    stats = db.category_stats()
    ov = db.override_stats()
    print("=" * 62)
    print("演示数据已生成 → %s（%s，%d 名学生）" % (db_path, CLASS_NAME, CLASS_SIZE))
    print("错题 %d 条 | 教师改判 %d 条（%.0f%%）"
          % (inserted, ov["modified"], ov["override_rate"] * 100))
    print("-" * 62)
    print("叙事主线（教师确认口径，按批次）：")
    from app import ontology
    for cat in STORYLINE:
        counts = []
        for ui in range(5):
            n = sum(
                1 for e in db.list_errors(confirmed=True, batch_no=UNITS[ui],
                                          limit=99999)
                if (e.get("teacher_override") or e.get("category_id")) == cat)
            counts.append(n)
        print("  %-10s %s" % (ontology.category_name(cat),
                             " → ".join(str(c) for c in counts)))
    print("=" * 62)
    print("下一步：.venv/bin/uvicorn app.main:app --port 8000")


if __name__ == "__main__":
    main()
