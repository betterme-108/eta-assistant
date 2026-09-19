"""生成全量真实感测试数据：两个班级 × 10 名学生 × 每生约 19 条历史错题。

数据真实性设计（非胡编乱造，全部对齐真实教学规律）：
  1. 学生能力分层：每班 5 档（正态分布），能力越强错题越少（12-27 条），
     且错因结构不同——优等生以 E02 粗心/D 类规范为主，薄弱生以 A/B 类知识为主；
  2. 班级画像差异：9年级20班语法薄弱（B01 时态/B06 主谓一致主线），
     9年级21班阅读与表达薄弱（C03 推断/C01 主旨主线）——两班决策单不同；
  3. 个人画像：每生 1-2 个主错因（占其错题 40-55%），同类错因跨批次聚集
     （触发"个别辅导名单"），题目均来自 scripts/seed_bank.py 真实题库；
  4. 教学干预叙事：主线错因随批次推进显著下降（上学期期中前高发 → 讲评后
     递减 → 九上期末见效 → 春季开学小幅反弹 → 一路下降）；
  5. 时间线真实：一学年 13 个批次（九上 2025 秋 4 次作业 + 2 次考试，
     九下 2026 春 4 次作业 + 3 次考试，均为周五），录入/确认
     的时间差符合教师工作节奏（当天录入、1-3 天确认）；
  6. 教师确认留痕：早期批次约 97% 已确认（accept/modify/ignore 比例真实），
     最后一批（九下期末考试）55% 待确认；改判对来自真实边界案例（B06→E02 粗心漏 s 等）。

用法：
  .venv/bin/python scripts/seed_full.py            # 追加写入默认库 runtime/db/eta.db
  .venv/bin/python scripts/seed_full.py --reset    # 清空后生成
  .venv/bin/python scripts/seed_full.py --db path  # 指定数据库
固定随机种子，任何人复现都得到同一份数据。同时导出摘要到 runtime/seed/seed_full.json。
"""
import argparse
import json
import os
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from seed_bank import BANK, DIFFICULTY  # noqa: E402

# 批次时间线：一学年（九年级上 2025 秋 + 九年级下 2026 春），均为周五收作业/考试。
# 课时作业与考试试卷穿插——错题确认 / 错因分析 / 讲评备课的「某次作业 /
# 某次考试」维度都有真实数据；时间跨度拉满一学年（2025-09 → 2026-06），
# 周 / 月 / 半年三种趋势粒度都有足够数据点。
BATCHES = [
    # (batch_no 唯一标识, exam_type 批改类型, date 录入日期)
    ("Unit 1", "课时作业", "2025-09-26"),
    ("Unit 2", "课时作业", "2025-10-17"),
    ("期中考试", "考试试卷", "2025-11-07"),
    ("Unit 3", "课时作业", "2025-11-21"),
    ("Unit 4", "课时作业", "2025-12-12"),
    ("九上期末考试", "考试试卷", "2026-01-09"),
    ("Unit 5", "课时作业", "2026-03-13"),
    ("Unit 6", "课时作业", "2026-03-27"),
    ("第一次月考", "考试试卷", "2026-04-10"),
    ("Unit 7", "课时作业", "2026-04-24"),
    ("半期考试", "考试试卷", "2026-05-08"),
    ("Unit 8", "课时作业", "2026-05-29"),
    ("九下期末考试", "考试试卷", "2026-06-19"),
]
UNITS = [b[0] for b in BATCHES]              # 唯一标识（batch_no）列表
N_BATCHES = len(BATCHES)

# 主线类别批次权重：干预叙事——上学期期中前高发，讲评干预后递减；
# 九上期末见效 → 寒假后春季开学小幅反弹（遗忘）→ 一路下降到九下期末。
STORYLINE_WEIGHTS = [1.50, 1.35, 1.30, 1.05, 0.85, 0.70,
                    0.90, 0.75, 0.65, 0.55, 0.45, 0.35, 0.30]

# 每班人数（小班规模：名单清爽、教师逐个面批可覆盖）
# 注意：决策单阈值已做班级规模自适应（decision.SKIP_MIN_COUNT），
# 改班级人数不需要同步改阈值；错题总量随人数线性变化。
CLASS_SIZE = 10

# 能力档 → (人数近似分布, 错题量区间)  各档人数之和 = CLASS_SIZE
# （一学年 13 个批次，区间比单学期略放宽，让趋势图分桶更饱满）
ABILITY_PLAN = {1: (2, (27, 30)), 2: (2, (24, 26)), 3: (3, (21, 23)),
                4: (2, (18, 20)), 5: (1, (15, 17))}

# 班级画像：主线错因（下降叙事）+ 次高发倾向（权重）+ 主线总量
CLASS_PROFILES = {
    "9年级20班": {
        "prefix": "920",
        "storyline": ["B01", "B06"],
        "storyline_counts": {"B01": 20, "B06": 17},
        "bias": {"B06": 1.6, "B04": 1.4, "A05": 1.3, "A03": 1.2, "B08": 1.2,
                 "B02": 1.1, "A01": 1.1, "D08": 1.1},
        "practice_cats": ["B01", "B06"],
    },
    "9年级21班": {
        "prefix": "921",
        "storyline": ["C03", "C01"],
        "storyline_counts": {"C03": 19, "C01": 16},
        "bias": {"D04": 1.5, "C04": 1.4, "C05": 1.3, "B08": 1.3, "B01": 1.2,
                 "C02": 1.2, "D05": 1.1, "C06": 1.1},
        "practice_cats": ["C03", "D04"],
    },
}

NAMES = {
    "9年级20班": [
        "王雨泽", "李思远", "张一诺", "陈嘉禾", "刘俊熙", "杨若曦", "赵晨曦", "黄子睿",
        "周芷晴", "吴博文", "林浩然", "何嘉怡", "郭明轩", "马晓蕾", "罗俊杰", "梁静怡",
        "宋子墨", "唐嘉铭", "许诺言", "韩雨欣", "冯天佑", "邓佳宁", "曹可欣", "彭浩宇",
        "曾雅婷", "肖博文", "田雨桐", "董思成", "袁明辉", "潘雨萱", "蔡文静", "蒋昕怡",
        "余子涵", "杜泽宇", "叶一涵", "程梓萱", "汪志远", "范晓萌", "石景行", "廖承志",
        "陆嘉树", "卢梦洁", "宋远航", "温书宁", "阮清越", "岳鹏程", "崔梦琪", "郑好",
        "沈书瑶", "白若溪",
    ],
    "9年级21班": [
        "张伟豪", "李欣怡", "王浩宇", "陈美琳", "刘诗涵", "杨帆", "赵子涵", "黄雅静",
        "周子轩", "吴思琪", "徐志强", "胡晓峰", "朱雨萱", "高一飞", "林小满", "何瑞霖",
        "马丽娟", "罗文博", "梁文昊", "宋佳怡", "唐启铭", "许家豪", "韩锐", "冯思雨",
        "邓紫嫣", "曹雪", "彭飞龙", "曾子豪", "肖雅雯", "田恬", "董家旺", "袁晓东",
        "潘玮", "蔡明杰", "蒋一鸣", "余悦", "杜佳琪", "叶星辰", "谢明宇", "汪俊宇",
        "范婷婷", "石磊", "廖静文", "陆展博", "卢静雯", "温以宁", "阮秋月", "崔子健",
        "郑凯", "沈砚秋",
    ],
}

# 教师改判对（AI 判 → 教师改判）：真实边界案例，仅作用于非主线类别
OVERRIDE_MAP = {
    "B06": "E02",   # 表面三单错误，实为粗心漏 s
    "A05": "A01",   # 冠词/不可数名词 → 拼写形近
    "D04": "B08",   # 中式英语内部：逐字直译 vs 语序并列
    "C01": "C03",   # 主旨题与推断题边界
    "C02": "C03",   # 细节定位与推断边界
    "D08": "E02",   # 大小写规范问题实为粗心
    "B04": "B01",   # 非谓语与时态混淆边界
}

# 确认行为比例（前 4 批已确认部分）
P_ACCEPT, P_IGNORE = 0.94, 0.03
P_MODIFY_COVERED = 0.38    # 边界类别（OVERRIDE_MAP 命中且非主线）的改判概率
                            # → 全库改判率约 8-10%（护AI 留痕核心数据）
P_PENDING_OLD = 0.03       # 早期批次漏审比例
P_PENDING_NEW = 0.45       # 最后一批（最新一次考试）待确认比例

ALL_CATS = sorted(BANK.keys())


def _pick(rng, population, weights):
    return rng.choices(population, weights=weights, k=1)[0]


def build_students(rng, class_name):
    """生成本班学生：[(code, name, ability)]，能力分层按 ABILITY_PLAN。"""
    profile = CLASS_PROFILES[class_name]
    abilities = []
    for level, (n, _rng_) in ABILITY_PLAN.items():
        abilities.extend([level] * n)
    abilities = abilities[:CLASS_SIZE]
    while len(abilities) < CLASS_SIZE:
        abilities.append(3)
    rng.shuffle(abilities)
    names = list(NAMES[class_name])
    rng.shuffle(names)
    return [("%s%02d" % (profile["prefix"], i + 1), names[i], abilities[i])
            for i in range(CLASS_SIZE)]


def largest_remainder(n, weights):
    """校 n 条按权重确定性分配到各批次（largest remainder 法）。"""
    total_w = sum(weights)
    raw = [n * w / total_w for w in weights]
    counts = [int(x) for x in raw]
    order = sorted(range(len(weights)), key=lambda i: raw[i] - counts[i], reverse=True)
    for i in order[:n - sum(counts)]:
        counts[i] += 1
    return counts


def assign_units(rng, cat, n):
    """把 n 条错题均匀分配到全部批次。

    主线类别已在班级级按递减权重确定性规划（见 gen_class）；
    本函数处理非主线类别：均匀分配。
    """
    if n <= 0:
        return [0] * N_BATCHES
    counts = [n // N_BATCHES] * N_BATCHES
    for i in rng.sample(range(N_BATCHES), n - sum(counts)):
        counts[i] += 1
    return counts


def gen_class(rng, class_name):
    """生成一个班的全部错题计划：[(student, ability, cat, batch_idx, bank_idx)]。

    两段式规划（贴近真实教学场景）：
    1. 主线类别班级级规划：总量固定、按递减权重分批次（叙事曲线平滑可控），
       其中约 60% 集中在“画像学生”（薄弱生，同类 3-5 次 → 触发个别辅导名单），
       其余按能力加权随机分配；
    2. 非主线类别个人画像：能力分层决定总量，个人主错因 ×1.8 权重，
       类别难度与能力匹配，均匀分布到各批次。
    """
    profile = CLASS_PROFILES[class_name]
    students = build_students(rng, class_name)
    plan = []
    per_cat_counter = Counter()          # 同类题号轮转（同班同题 → 班级基准真实）

    def add(code, ability, cat, ui):
        per_cat_counter[cat] += 1
        plan.append((code, ability, cat, ui,
                     (per_cat_counter[cat] // 3) % len(BANK[cat])))

    # ── 1) 主线班级级规划 ──
    weak_pool = [s for s in students if s[2] <= 3]
    focus_students = rng.sample(weak_pool, min(len(weak_pool), 10))
    ability_w = {1: 5.0, 2: 3.0, 3: 1.6, 4: 0.7, 5: 0.3}
    for cat in profile["storyline"]:
        total_n = profile["storyline_counts"][cat]
        unit_counts = largest_remainder(total_n, STORYLINE_WEIGHTS)
        focus_quota = {}                     # 画像学生先定量（3-5 条同类错因）
        remaining = total_n
        for code, _n, _ab in focus_students:
            k = min(rng.choice([3, 4]), remaining)
            focus_quota[code] = focus_quota.get(code, 0) + k
            remaining -= k
            if remaining < total_n * 0.55:
                break
        for ui, n_unit in enumerate(unit_counts):
            for _ in range(n_unit):
                if focus_quota and rng.random() < 0.6:
                    candidates = [c for c, k in focus_quota.items() if k > 0]
                    if candidates:
                        code = rng.choice(candidates)
                        focus_quota[code] -= 1
                    else:
                        code = _pick(rng, [s[0] for s in students],
                                     [ability_w[s[2]] for s in students])
                else:
                    code = _pick(rng, [s[0] for s in students],
                                 [ability_w[s[2]] for s in students])
                add(code, 3, cat, ui)

    # ── 2) 非主线个人画像 ──
    storyline_set = set(profile["storyline"])
    for code, _name, ability in students:
        lo, hi = ABILITY_PLAN[ability][1]
        # 主线已占额度：从该生额度中扣除已规划的主线条数
        used = sum(1 for p in plan if p[0] == code)
        total = max(4, rng.randint(lo, hi) - used)
        weights = {}
        pool_cats = [c for c in ALL_CATS if c not in storyline_set]
        focus_n = 2 if ability <= 2 else 1
        # 个人主错因从知识/表达/阅读/规范类中选（E 类是复核标签，不作主错因放大）
        focus = rng.sample([c for c in pool_cats
                            if not c.startswith("E")], focus_n)
        for cat in pool_cats:
            w = profile["bias"].get(cat, 1.0)
            if cat in focus:
                w *= 1.8
            diff = DIFFICULTY.get(cat, 2)
            if ability <= 2:
                w *= {1: 1.3, 2: 1.15, 3: 0.9, 4: 0.6}[diff]
            elif ability >= 4:
                w *= {1: 1.25 if cat.startswith(("E", "D")) else 0.85,
                      2: 0.9, 3: 1.0, 4: 1.1}[diff]
            weights[cat] = w
        cats = []
        while len(cats) < total:
            cats.append(_pick(rng, pool_cats, [weights[c] for c in pool_cats]))
        cat_counts = Counter(cats)
        for cat, n in cat_counts.items():
            per_unit = assign_units(rng, cat, n)
            for ui, cnum in enumerate(per_unit):
                for _ in range(cnum):
                    add(code, ability, cat, ui)

    rng.shuffle(plan)
    return students, plan


def exam_time(rng, ui):
    """作业/考试当天下午/晚间录入时间。"""
    base = datetime.strptime(BATCHES[ui][2], "%Y-%m-%d")
    return base.replace(hour=rng.randint(15, 20), minute=rng.randint(0, 59),
                        second=rng.randint(0, 59))


def evidence_for(cat, wrong, signals_first, cat_name):
    # 判断依据面向教师展示：用错因名称，不暴露内部编号（如 C04）
    return "作答 %r 符合「%s」的典型特征（%s）" % (wrong[:40], cat_name, signals_first)


# 阅读理解/完形填空的演示原文池（同题同文：bank_idx 决定，保证可复现）
_READING_PASSAGES = [
    "Tom was a quiet boy. He liked reading books about science. One day, he found a "
    "sick bird in the park and took it home. He looked after it for two weeks until "
    "it could fly again. His classmates learned a lot about caring for animals from him.",
    "Many students in our school read e-books now. They say it is easy to carry hundreds "
    "of books in one phone. Also, e-books are often cheaper than paper books, so students "
    "can read more without spending too much money.",
    "Last summer, Li Hua visited his uncle in the countryside. Every morning he helped "
    "feed the chickens and water the vegetables. In the evening, his uncle told him "
    "stories about the stars. It was a busy but happy holiday.",
    "Our school will hold a Reading Week from May 10 to May 14. During the week, students "
    "can share their favorite books in class, join the book market on the playground, "
    "and meet a famous children's writer on Friday afternoon.",
]
_CLOZE_PASSAGE = (
    "Mr. Smith is a friendly teacher. He has taught English 1 twenty years. He always "
    "tells interesting stories to make his class 2 . After class, he often helps "
    "students 3 their problems, so everyone in the school likes him."
)


def _passage_for(qtype: str, bank_idx: int) -> str:
    """阅读/完形类错题携带原文（题型差异化呈现）；其余题型为空。"""
    if qtype == "完形填空":
        return _CLOZE_PASSAGE
    if "阅读" in (qtype or ""):
        return _READING_PASSAGES[bank_idx % len(_READING_PASSAGES)]
    return ""


# 已审校练习的确定性题组（演示用；真实练习由教师页面向 AI 生成并审校）
_SEED_PRACTICES = {
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
    "B06": [
        {"type": "单选", "q": "My sister ____ playing the piano every evening.",
         "options": ["like", "likes", "liked", "are liking"],
         "answer": "B", "explanation": "My sister 三单，一般现在时动词加 s"},
        {"type": "单选", "q": "Everyone in our class ____ an English dictionary.",
         "options": ["have", "has", "there is", "are having"],
         "answer": "B", "explanation": "everyone 按三单处理"},
        {"type": "改错", "q": "改正句子：He like playing basketball.",
         "answer": "He likes playing basketball.", "explanation": "三单主语动词加 s"},
    ],
    "D04": [
        {"type": "单选", "q": "It's dark. Please ____ the light.",
         "options": ["open", "turn on", "turn off", "close"],
         "answer": "B", "explanation": "'开灯'是 turn on the light，open 是中式直译"},
        {"type": "单选", "q": "Which sentence is correct English?",
         "options": ["I very like this song.", "I like this song very much.",
                     "I like very much this song.", "Very I like this song."],
         "answer": "B", "explanation": "very 不能直接修饰动词，程度状语置句尾"},
        {"type": "改错", "q": "改正句子：Please open the light.",
         "answer": "Please turn on the light.", "explanation": "固定搭配 turn on"},
    ],
    "C03": [
        {"type": "阅读理解",
         "passage": "Many students in our school read e-books now. They say it is easy "
                    "to carry hundreds of books in one phone. Also, e-books are often "
                    "cheaper than paper books, so students can read more without spending too much.",
         "q": "What can we learn from the passage?",
         "options": ["E-books are bad for eyes.", "Students find e-books convenient to carry.",
                     "Phones will replace paper books soon.", "The writer wants students to stop buying paper books."],
         "answer": "B",
         "explanation": "A/C/D 文中未提及或无法推出——推断必须基于文本、止步文本；B 是 easy to carry 的同义改写"},
        {"type": "阅读理解",
         "passage": "Tom planted a small tree in front of his house five years ago. "
                    "He watered it every week. Now the tree is taller than the windows "
                    "and gives shade in summer.",
         "q": "What can we infer?",
         "options": ["Tom waters the tree every day.", "The tree has grown over five years.",
                     "Tom's house is very old.", "Nobody likes the tree."],
         "answer": "B",
         "explanation": "A/C/D 无文本依据；five years ago → now 说明树长了五年"},
        {"type": "判断", "q": "推断题三步自查：①选项信息能否在文中找到出处？②是否把'可能'当'必须'？③是否用生活常识替代了文本依据？",
         "answer": "每一步都应答'是'", "explanation": "推断题必须'基于文本、止步文本'"},
    ],
}


def _seed_practice_items(cid: str):
    return [dict(it) for it in _SEED_PRACTICES.get(cid, [])] or _SEED_PRACTICES["B01"]


def main() -> None:
    parser = argparse.ArgumentParser(description="生成两个班级的全量真实感测试数据")
    parser.add_argument("--db", default=None, help="数据库路径（默认 runtime/db/eta.db）")
    parser.add_argument("--reset", action="store_true", help="先清空旧库")
    parser.add_argument("--json", default=None, help="摘要 JSON 输出路径（默认 runtime/seed/seed_full.json）")
    args = parser.parse_args()

    if args.db:
        os.environ["ETA_DB"] = args.db
    # 种子数据全部确定性生成（可复现、不依赖外部 API）：
    # 错题直接指定归因类别；练习由脚本内置题组直插并置为已审校。
    from app import config, db, ontology
    from app.services import practice
    db_path = config.get_db_path()
    if args.json:
        json_path = args.json
    elif db_path == os.path.join(config.DB_DIR, "eta.db"):
        json_path = os.path.join(config.RUNTIME_DIR, "seed", "seed_full.json")
    else:
        json_path = os.path.join(os.path.dirname(db_path), "seed_full.json")

    if args.reset and os.path.exists(db_path):
        os.remove(db_path)
        print("已清空旧库：%s" % db_path)
    db.init_db()

    rng = random.Random(20260905)          # 固定种子：可复现
    signals_map = {c["id"]: c["signals"][0] for c in ontology.all_categories()}
    name_map = {c["id"]: c["name"] for c in ontology.all_categories()}

    summary = {"classes": {}, "generated_at": datetime.now().isoformat(timespec="seconds")}
    total_inserted = 0

    for class_name in CLASS_PROFILES:
        profile = CLASS_PROFILES[class_name]
        students, plan = gen_class(rng, class_name)

        # 1) 学生名单（姓名仅本地映射；出参只有代号）
        db.import_students([{"code": code, "name": name, "class": class_name}
                            for code, name, _ab in students])

        # 2) 错题录入 + 确认
        confirmed_history = defaultdict(int)   # (code, cat) 已确认次数 → behavior 复核
        inserted = 0
        confirmed = modified = ignored = pending = 0
        error_ids_by_unit = [[] for _ in range(N_BATCHES)]

        for code, ability, cat, ui, bank_idx in sorted(
                plan, key=lambda x: (x[3], x[0])):
            q, wrong, correct, qtype = BANK[cat][bank_idx]
            created = exam_time(rng, ui)
            conf = round(rng.uniform(0.86, 0.97) if cat in profile["storyline"]
                         else rng.uniform(0.68, 0.95), 2)
            # behavior 复核（E 类判别参考）：历史 ≥3 → E05 未掌握；=1 且偶发 → E02 粗心
            behavior = None
            hist = confirmed_history[(code, cat)]
            if not cat.startswith("E"):
                if hist + 1 >= 3:
                    behavior = {"tag": "E05",
                                "note": "该生该错因已出现 %d 次——判断为知识未掌握，建议列入个别辅导" % (hist + 1),
                                "student_cat_count": hist + 1, "class_correct_rate": 0.62}
                elif hist == 0 and rng.random() < 0.4:
                    behavior = {"tag": "E02",
                                "note": "该错因仅出现 1 次且班级正确率 88%——判断为粗心性失误，建议习惯训练而非重讲",
                                "student_cat_count": 1, "class_correct_rate": 0.88}

            eid = db.insert_error({
                "student_code": code, "class_name": class_name,
                "question": q, "answer": wrong, "correct": correct, "qtype": qtype,
                # 阅读/完形类错题携带原文（题型差异化呈现）
                "passage": _passage_for(qtype, bank_idx),
                "exam_type": BATCHES[ui][1], "batch_no": BATCHES[ui][0],
                "category_id": cat,
                "evidence": evidence_for(cat, wrong, signals_map.get(cat, ""),
                                         name_map.get(cat, cat)),
                "teaching_point": "",
                "confidence": conf, "needs_review": 0,
                "behavior": behavior,
                "created_at": created.isoformat(timespec="seconds"),
            })
            inserted += 1
            error_ids_by_unit[ui].append(eid)

            # 教师确认：除最后一批（最新一次考试未审完）外少量漏审
            pending_p = P_PENDING_NEW if ui == N_BATCHES - 1 else P_PENDING_OLD
            if rng.random() < pending_p:
                pending += 1
                continue
            if rng.random() < P_IGNORE:
                action, override = "ignore", None
            elif (cat in OVERRIDE_MAP and cat not in profile["storyline"]
                  and rng.random() < P_MODIFY_COVERED):
                action, override = "modify", OVERRIDE_MAP[cat]
            else:
                action, override = "accept", None
            confirmed_at = (created + timedelta(days=rng.randint(1, 3))).isoformat(timespec="seconds")
            db.confirm_error(eid, action, override, confirmed_at=confirmed_at)
            confirmed_history[(code, cat)] += 1
            if action == "accept":
                pass
            elif action == "modify":
                modified += 1
            else:
                ignored += 1

        # 3) 已审校补偿练习（决策单"配套练习"演示）——确定性直插，不依赖 LLM
        for cid in profile["practice_cats"]:
            pid = db.insert_practice(cid, _seed_practice_items(cid), class_name=class_name)
            practice.approve(pid)

        # 4) 班级摘要
        cat_stats = {s["category_id"]: s["count"] for s in db.category_stats(class_name=class_name)}
        storyline_curve = {}
        for cat in profile["storyline"]:
            curve = []
            for ui in range(N_BATCHES):
                n = sum(
                    1 for e in db.list_errors(confirmed=True, class_name=class_name,
                                              batch_no=UNITS[ui], limit=99999)
                    if (e.get("teacher_override") or e.get("category_id")) == cat)
                curve.append(n)
            storyline_curve[cat] = curve
        summary["classes"][class_name] = {
            "students": len(students),
            "errors": inserted,
            "confirmed": confirmed,
            "pending": pending,
            "modified": modified,
            "ignored": ignored,
            "storyline_curve": storyline_curve,
            "top5": dict(Counter(cat_stats).most_common(5)),
        }
        total_inserted += inserted
        print("[%s] 学生 %d 人 | 错题 %d 条（待确认 %d，改判 %d，忽略 %d）"
              % (class_name, len(students), inserted, pending, modified, ignored))
        for cat, curve in storyline_curve.items():
            print("  主线 %-8s %s" % (ontology.category_name(cat),
                                     " → ".join(str(c) for c in curve)))

    # 5) 导出摘要 JSON（仅代号统计，无姓名）
    summary["total_errors"] = total_inserted
    summary["total_students"] = sum(c["students"] for c in summary["classes"].values())
    os.makedirs(os.path.dirname(json_path) or ".", exist_ok=True)   # 新环境 runtime/seed/ 可能不存在
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    ov = db.override_stats()
    print("=" * 66)
    print("全量测试数据已生成 → %s（2 个班级 / %d 名学生 / %d 条错题）"
          % (db_path, summary["total_students"], total_inserted))
    print("教师改判 %d 条（修正率 %.1f%%）| 摘要 → %s"
          % (ov["modified"], ov["override_rate"] * 100, json_path))
    print("启动：.venv/bin/uvicorn app.main:app --port 8000")


if __name__ == "__main__":
    main()
