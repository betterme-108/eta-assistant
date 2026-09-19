"""真实错题题库（36 类本体库 v3 × 每类 1-7 道真实初中英语题）。

用于 scripts/seed_full.py 生成贴近真实教学场景的测试数据。
每条：(题干, 学生错误作答, 正确答案, 题型)——错误作答形态严格对应该类
本体库 signals（见 data/error_ontology_en.yaml），保证归因有据可查。

编号对齐 v3 本体：A 词汇知识 / B 语法结构 / C 语篇理解 / D 书面表达 / E 答题行为；
两大任务（课时作业 / 考试试卷）的错题均从此库抽样。
"""

BANK = {
    "A01": [  # 拼写与形近词
        ("Please keep ____ (quiet) in the library.", "quite", "quiet", "词语运用"),
        ("I don't know ____ it will rain tomorrow. (是否)", "weather", "whether", "词语运用"),
        ("He ____ (receive) a letter from his friend last week.", "recieved（拼写错误）", "received", "词语运用"),
        ("I can't find my key ____ (任何地方).", "somewhere", "anywhere", "词语运用"),
    ],
    "A02": [  # 词形变化
        ("There are many ____ (sheep) on the farm.", "sheeps", "sheep", "词语运用"),
        ("He runs ____ (quick).", "quick（未变形直接抄原形）", "quickly", "词语运用"),
        ("I am ____ in the ____ news. (interest)", "interesting; interested", "interested; interesting", "词语运用"),
    ],
    "A03": [  # 词义辨析
        ("He ____ me a story last night.", "said", "told", "单选"),
        ("I ____ a book from the school library yesterday.", "lent", "borrowed", "单选"),
        ("Don't forget to ____ the lights when you leave.", "close", "turn off", "单选"),
        ("He has ____ his homework already. / He ____ his homework an hour ago.（finish）", "has finished（用于一般过去时语境）",
         "finished", "词语运用"),
    ],
    "A04": [  # 固定搭配与短语
        ("He often listens ____ the radio in the morning.", "listen the radio（缺 to）",
         "listen to the radio", "词语运用"),
        ("We have no classes ____ Sunday.", "in Sunday", "on Sunday", "单选"),
        ("He arrived ____ Beijing last night.", "arrived Beijing（缺 in）", "arrived in Beijing", "词语运用"),
        ("Thank you ____ helping me with my English.", "of", "for", "单选"),
        ("Translation: 我们应该学习更多的知识。", "We should learn more knowledge.",
         "We should gain more knowledge.", "书面表达"),
        ("I'll ____ my homework after dinner.", "make", "do", "单选"),
        ("Translation: 他英语说得很好。", "He says English very well.", "He speaks English very well.", "书面表达"),
    ],
    "A05": [  # 冠词与限定词
        ("He is ____ honest boy, and we all trust him.", "a", "an", "单选"),
        ("My father bought me ____ useful book last week.", "an", "a", "单选"),
        ("Fill in the blank: The story happened in ____ (a European country).", "an European country", "a European country", "词语运用"),
    ],
    "A06": [  # 代词与指代
        ("The teacher asked you and ____ to stay.", "I", "me", "单选"),
        ("This book is ____, not yours.", "my", "mine", "单选"),
        ("Between you and ____, the exam was hard.", "I", "me", "填空"),
        ("She washed the clothes by ____ (she).", "her", "herself", "词语运用"),
    ],
    "B01": [  # 时态误用
        ("He ____ (go) to the park yesterday.", "has gone", "went", "词语运用"),
        ("I ____ (see) the film two days ago.", "have seen", "saw", "词语运用"),
        ("My father ____ (buy) the bike for me last month.", "has bought", "bought", "词语运用"),
        ("She ____ (live) here since 2020.", "lived", "has lived", "词语运用"),
        ("When I got home, my mother ____ (cook) dinner.", "cooks", "was cooking", "词语运用"),
    ],
    "B02": [  # 被动语态
        ("The bridge ____ (build) in 1995.", "was build", "was built", "词语运用"),
        ("English ____ by people all over the world.", "speaks", "is spoken", "单选"),
        ("Trees ____ (plant) on the hill every spring.", "plant", "are planted", "词语运用"),
        ("A new school ____ (build) in our town next year.", "will build", "will be built", "词语运用"),
    ],
    "B03": [  # 情态动词
        ("You'd better ____ too much junk food.", "to not eat", "not eat", "单选"),
        ("He could ____ (swim) when he was five.", "to swim", "swim", "词语运用"),
        ("Must I finish the work now? — No, you ____.", "mustn't", "needn't", "单选"),
        ("You should ____ (listen) to the teacher carefully in class.", "listening", "listen", "词语运用"),
    ],
    "B04": [  # 非谓语动词
        ("He enjoys ____ (listen) to music after school.", "to listen", "listening", "词语运用"),
        ("I finished ____ (do) my homework at nine.", "to do", "doing", "词语运用"),
        ("Would you mind ____ (open) the window?", "to open", "opening", "单选"),
        ("The teacher told us ____ (not be) late again.", "not being", "not to be", "词语运用"),
    ],
    "B05": [  # 从句与连接词
        ("Translation: 我不知道他为什么迟到。", "I don't know why is he late.",
         "I don't know why he is late.", "书面表达"),
        ("Could you tell me ____?", "where does he live", "where he lives", "单选"),
        ("Do you know ____ tomorrow?", "what will the weather be like",
         "what the weather will be like", "单选"),
    ],
    "B06": [  # 主谓一致与 there be
        ("Choose the correct sentence.", "He like playing basketball after school.",
         "He likes playing basketball after school.", "单选"),
        ("Everyone in our class ____ an English dictionary.", "have", "has", "单选"),
        ("My sister ____ (watch) TV every evening.", "watch", "watches", "词语运用"),
        ("There ____ a book and two pens on the desk.", "are", "is", "单选"),
        ("Translation: 山上有许多树。", "There have many trees on the hill.",
         "There are many trees on the hill.", "书面表达"),
        ("There ____ a lot of water in the bottle.", "have", "is", "单选"),
        ("There ____ going to be a meeting tomorrow.", "is", "are", "单选"),
    ],
    "B07": [  # 比较等级
        ("This book is ____ than that one.", "more better", "much better", "单选"),
        ("Tom runs as ____ (quick) as his brother.", "quick", "quickly", "词语运用"),
        ("Lucy is ____ (tall) than Lily.", "more tall", "taller", "词语运用"),
        ("The ____ (many) trees we plant, the better our city will be.", "much", "more", "词语运用"),
        ("Our life is getting ____.", "more and more better", "better and better", "单选"),
    ],
    "B08": [  # 句子成分与语序
        ("Choose the correct sentence.", "He very happy today.", "He is very happy today.", "单选"),
        ("Translation: 我和我的朋友们昨天去游泳了。", "I with my friends went swimming yesterday.",
         "My friends and I went swimming yesterday.", "书面表达"),
        ("I don't like math. — I don't like it, ____.", "also", "either", "单选"),
        ("Translation: 他也是一个学生。", "He also is a student, too.", "He is also a student.", "书面表达"),
        ("Choose the correct sentence.", "The book, it is very interesting.",
         "The book is very interesting.", "单选"),
    ],
    "B09": [  # 词性误用
        ("The story is very ____ (interest).", "interest", "interesting", "词语运用"),
        ("I am ____ in the ____ news. (interest)", "interesting; interested", "interested; interesting", "词语运用"),
    ],
    "C01": [
        ("What's the best title for the passage?（短文：男孩照顾流浪狗并鼓励自己不放弃）",
         "A Sad Story About a Dog", "Never Give Up: A Boy and His Dog", "阅读理解"),
        ("What's the main idea of Para. 2?（短文：城市绿化好处）",
         "Trees can make the air clean.", "Why our city needs more green", "阅读理解"),
        ("What is the passage mainly about?", "The writer's breakfast.", "The writer's healthy lifestyle.", "阅读理解"),
    ],
    "C02": [
        ("Which of the following is NOT true according to Para. 3?",
         "选了文中未出现的细节项 B. The school was built in 2010.",
         "选对 D. The school has 2,000 students now.", "阅读理解"),
        ("According to the passage, when did the reading week start?",
         "选了第二段的 Monday（实际是第三段的 Wednesday）", "Wednesday", "阅读理解"),
        ("Who taught the writer to swim?", "选了 Uncle Li（实际是 Father）", "The writer's father", "阅读理解"),
    ],
    "C03": [
        ("What can we infer from the last paragraph?",
         "The writer will give up music forever.", "The writer may keep music as a hobby.", "阅读理解"),
        ("What can we learn from the passage?", "People must stop using phones at once.",
         "We should use phones in a proper way.", "阅读理解"),
        ("The writer mentions the numbers in Para. 2 to ____.",
         "show that everyone likes shopping online", "give facts to support his idea", "阅读理解"),
    ],
    "C04": [
        ("The underlined word \"support\" in Para. 2 most probably means ____.",
         "doubt（猜成\"怀疑\"）", "help and encouragement", "阅读理解"),
        ("What does the word \"bright\" mean in the sentence \"He is a bright boy\"?",
         "明亮的（本义）", "聪明的（语境义）", "阅读理解"),
        ("The underlined word \"them\" in Para. 1 refers to ____.",
         "the teachers（指代错误）", "the books", "阅读理解"),
    ],
    "C05": [
        ("What's the writer's attitude toward online learning?",
         "negative（文本实为支持）", "positive", "阅读理解"),
        ("The writer wrote the passage to ____.",
         "sell a new kind of book（误读写作目的）", "share his experience of learning English", "阅读理解"),
        ("Why does the writer tell the story of his grandfather?",
         "To make the passage longer.", "To show that hard work matters.", "阅读理解"),
    ],
    "C06": [
        ("What does \"it\" in the sentence \"It changed my life\" refer to?",
         "The weather（指代错误）", "The reading habit", "阅读理解"),
        ("Which paragraph gives the conclusion of the passage?",
         "Para. 2（选错段落）", "Para. 5", "阅读理解"),
        ("How is the passage organized?", "按时间顺序（实为总分结构）", "总—分—总结构", "阅读理解"),
    ],
    "C07": [  # 完形语境线索缺失
        ("完形填空第 21 题（前文：他经常帮助同学；后文：大家都很喜欢他）",
         "选了 angry（未结合上下文线索）", "friendly", "完形填空"),
        ("完形填空第 22 题（前文提到下雨，且后文说衣服湿了）",
         "选了 sunny（忽略语篇线索）", "rainy", "完形填空"),
    ],
    "C08": [  # 信息转述与整合
        ("阅读简答：用不超过 10 个词概括作者周末做了哪两件事。",
         "整段照抄原文第 2 段", "He read books and played sports.", "阅读理解"),
        ("任务型阅读：根据表格补全 5 项信息。", "漏填了 Price 一栏（4/5 项）", "5 项信息完整", "阅读理解"),
    ],
    "D01": [  # 内容要点缺失
        ("书面表达：介绍你的学校生活，并给出两条学习建议。", "只写了学校生活介绍，未写建议（漏任务点）",
         "介绍 + 两条建议（任务点齐全）", "书面表达"),
        ("阅读简答：请用不超过 5 个词回答，并写出三条理由。", "理由只写了一条", "三条理由齐全", "阅读理解"),
        ("表格填数：按表格补全 4 个数字。", "第 3 个数字串位（填错行）", "4 个数字对应正确", "任务型阅读"),
    ],
    "D02": [  # 文体与格式错误
        ("书面表达：写一封给朋友的信（80 词）。", "一段到底，无称呼无落款", "有称呼、正文分段、落款", "书面表达"),
        ("书面表达：写一份活动通知。", "缺标题与落款，正文挤成一段", "标题 + 正文 + 落款齐全", "书面表达"),
    ],
    "D03": [  # 人称与时态不符
        ("书面表达：My Best Friend（80 词）", "人称前后不一致：先写 I have a friend. He likes…中间又写 I likes…",
         "人称一致，草稿誊写正确", "书面表达"),
        ("书面表达：A Happy Day（80 词，写上周日）", "开头 I go to the park（通篇用一般现在时）",
         "I went to the park", "书面表达"),
    ],
    "D04": [  # 中式英语表达
        ("Translation: 我非常喜欢英语。", "I very like English.", "I like English very much.", "书面表达"),
        ("It's dark. Please ____ the light.", "open", "turn on", "单选"),
        ("Translation: 我妈妈正在打电话。", "My mother is playing the phone.", "My mother is making a phone call.", "书面表达"),
        ("Translation: 我们应该好好学习。", "We should good good study.", "We should study hard.", "书面表达"),
        ("Choose the correct sentence.", "He returned back home at six.",
         "He returned home at six.", "单选"),
    ],
    "D05": [  # 衔接与连贯缺失
        ("Choose the correct sentence.", "Although he was ill, but he didn't come to school.",
         "Although he was ill, he didn't come to school.", "单选"),
        ("Choose the correct sentence.", "Because he was ill, so he didn't come.",
         "Because he was ill, he didn't come.", "单选"),
        ("____ she is young, she knows a lot.", "But", "Though", "单选"),
    ],
    "D06": [  # 语用不得体
        ("书面表达片段：写信给外国朋友介绍学校生活", "…and a lot of kids wanna play games after class, you know…",
         "…and many children want to play games after class…", "书面表达"),
        ("书面表达片段：写信结尾", "That's all. Bye-bye!",
         "I'm looking forward to your reply. Best wishes!", "书面表达"),
    ],
    "D07": [  # 字数不足或超限
        ("书面表达：My Dream Job（80 词左右）", "只写了 40 词左右，结尾未完成", "80 词左右结构完整", "书面表达"),
        ("书面表达：My Family（80 词左右）", "写了两句即停笔", "80 词左右结构完整", "书面表达"),
    ],
    "D08": [  # 卷面与书写规范
        ("Fill in the blank: ____ (I) favorite subject is music.", "i（未大写）", "My", "词语运用"),
        ("书面表达片段：句子开头字母大写检查", "next week, we will have a sports meeting.（句首未大写）",
         "Next week, we will have a sports meeting.", "书面表达"),
    ],
    "E01": [  # 审题偏差
        ("阅读简答：What does the writer do on weekends?", "整段抄写原文第 2 段（未作答提炼）",
         "用自己的话概括：He usually reads and plays sports.", "阅读理解"),
        ("补全对话：问周末计划", "答了上周末做了什么（时态与话题均答非所问）", "回答本周末计划", "补全对话"),
    ],
    "E02": [  # 粗心性失误
        ("He ____ (be) to Shanghai twice.", "be（抄题漏写 been，属低级抄错）", "has been", "词语运用"),
        ("单选题：She ____ to school by bike every day. A. go B. goes", "答题卡涂错位置（对答案错位）", "B", "单选"),
        ("Choose the correct answer: Tom ____ his homework at 8 last night.", "knows the answer but circled A（涂卡笔误）",
         "was doing", "单选"),
    ],
    "E03": [  # 检查缺失
        ("词语运用：短文填词（10 空）", "草稿正确，誊写时第 6 空串行抄错", "10 空誊写无误", "词语运用"),
        ("单项选择 5 题（做完后未检查）", "第 3 题漏涂答题卡", "按作答涂卡", "单选"),
    ],
    "E04": [  # 空白未答
        ("完形填空第 12 题", "未作答（空白）", "D", "完形填空"),
        ("阅读理解第 44 题", "未作答（空白）", "B", "阅读理解"),
        ("书面表达", "空白未写", "80 词作文", "书面表达"),
    ],
    "E05": [  # 知识未掌握
        ("He ____ (go) to the park yesterday.（同类型时态题第 4 次出错）", "has gone（与 Unit 1 同类错误）", "went", "词语运用"),
        ("What can we infer from the passage?（推断题第 3 次同类出错）", "过度推断项", "文本内合理推断", "阅读理解"),
    ],
}

# 各类别在题库中的难度权重（供不同能力学生选取）：1=基础题 2=中档题 3=较难 4=难
DIFFICULTY = {
    "A01": 2, "A02": 2, "A03": 2, "A04": 2, "A05": 1, "A06": 1,
    "B01": 1, "B02": 2, "B03": 2, "B04": 2, "B05": 3, "B06": 1,
    "B07": 2, "B08": 2, "B09": 2,
    "C01": 3, "C02": 3, "C03": 4, "C04": 3, "C05": 4, "C06": 3,
    "C07": 3, "C08": 4,
    "D01": 3, "D02": 3, "D03": 3, "D04": 1, "D05": 2, "D06": 3,
    "D07": 3, "D08": 2,
    "E01": 3, "E02": 1, "E03": 2, "E04": 1, "E05": 4,
}
