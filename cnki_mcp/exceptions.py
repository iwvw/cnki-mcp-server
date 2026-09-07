"""CNKI MCP 自定义异常层级"""


class CNKIError(Exception):
    """CNKI 服务基础异常"""


class BrowserError(CNKIError):
    """浏览器启动/导航失败"""


class SearchError(CNKIError):
    """搜索无结果/搜索失败"""


class DetailError(CNKIError):
    """论文详情解析失败"""


class ValidationError(CNKIError):
    """参数校验失败"""


class CitationError(CNKIError):
    """引文格式化失败"""


class ExportError(CNKIError):
    """导出失败"""
