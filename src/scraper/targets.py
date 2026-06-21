"""
爬取目标配置。

每个目标的字段:
  name:    目标名称（用于日志）
  url:     爬取 URL
  parser:  解析器类型 ("icourse163" | "bilibili" | "generic")
  keywords: 课程关键词列表，用于过滤和匹配
  type:    内容类型 ("course_intro" | "video_info" | "article")
"""

SCRAPE_TARGETS = [
    # ── 金融学课程 ──
    {
        "name": "金融学基础-MOOC",
        "url": "https://www.icourse163.org/search.htm?search=金融学基础",
        "parser": "icourse163",
        "keywords": ["金融学", "货币银行", "金融市场", "金融学基础"],
        "type": "course_intro",
    },
    {
        "name": "公司金融-MOOC",
        "url": "https://www.icourse163.org/search.htm?search=公司金融",
        "parser": "icourse163",
        "keywords": ["公司金融", "企业财务", "资本结构", "NPV"],
        "type": "course_intro",
    },
    {
        "name": "金融学-B站",
        "url": "https://api.bilibili.com/x/web-interface/search/type?search_type=video&keyword=金融学基础",
        "parser": "bilibili",
        "keywords": ["金融学", "金融学基础", "货币银行学"],
        "type": "video_info",
    },

    # ── 数据科学课程 ──
    {
        "name": "数据科学-MOOC",
        "url": "https://www.icourse163.org/search.htm?search=数据科学导论",
        "parser": "icourse163",
        "keywords": ["数据科学", "Python", "统计分析", "机器学习"],
        "type": "course_intro",
    },
    {
        "name": "机器学习-MOOC",
        "url": "https://www.icourse163.org/search.htm?search=机器学习基础",
        "parser": "icourse163",
        "keywords": ["机器学习", "监督学习", "无监督学习", "深度学习"],
        "type": "course_intro",
    },
    {
        "name": "数据科学-B站",
        "url": "https://api.bilibili.com/x/web-interface/search/type?search_type=video&keyword=数据科学入门",
        "parser": "bilibili",
        "keywords": ["数据科学", "Python", "数据分析"],
        "type": "video_info",
    },

    # ── 法学课程 ──
    {
        "name": "法理学-MOOC",
        "url": "https://www.icourse163.org/search.htm?search=法理学",
        "parser": "icourse163",
        "keywords": ["法理学", "法律基础", "法治", "法的概念"],
        "type": "course_intro",
    },
    {
        "name": "民商法-MOOC",
        "url": "https://www.icourse163.org/search.htm?search=民商法基础",
        "parser": "icourse163",
        "keywords": ["民商法", "民法", "合同法", "侵权责任法"],
        "type": "course_intro",
    },
    {
        "name": "法学-B站",
        "url": "https://api.bilibili.com/x/web-interface/search/type?search_type=video&keyword=法理学入门",
        "parser": "bilibili",
        "keywords": ["法学", "法理学", "法律"],
        "type": "video_info",
    },
]
