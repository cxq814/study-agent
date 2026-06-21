# 1. 导入模块
from src.tools.base_tools import get_current_user, format_markdown_table
from src.tools.sqlite_tools import tool_get_user, tool_list_courses
from src.tools.redis_tools import tool_redis_status
from src.tools.rag_tools import RAGToolset

# 2. 检查Redis连接状态
print(tool_redis_status())

# 3. 读取测试用户（用你现有测试user_id，比如 "user_001"）
user = get_current_user("u001")
print("用户信息：", user)

# 4. 读取课程列表
courses = tool_list_courses()
print("课程数量：", len(courses))

# 5. 测试表格格式化（纯工具函数，无依赖）
table = format_markdown_table(["课程号","课程名"], [["FIN101","金融学"]])
print(table)

# 6. 测试RAG检索
rag = RAGToolset()
res = rag.search_courses("金融")
print("检索结果：", res)