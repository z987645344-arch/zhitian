"""专家回答的隐藏来源协议；只扣住可能的协议前缀，不缓冲正常正文。"""
import re

PROMPT = (
    "\n来源编号仅用于服务端处理，不要在回答正文中写编号。"
    "在完整回答最末尾另起一行，严格输出〔依据:1,3〕这样的标记，"
    "其中只列实际支撑本次回答的知识库资料编号（含同节补充资料），英文逗号分隔、不得解释。"
    "不得仅因资料出现在输入里就列入；附件没有知识库编号，不列入。"
    "知识库拒答可不输出标记；除此之外，标记必须是最后一行，不得写在正文或代码块中。"
)


class MarkerFilter:
    PREFIX = "〔依据"
    MAX_MARKER_CHARS = 256

    def __init__(self, candidate_count):
        self.candidate_count = candidate_count
        self.pending = ""
        self.marker = None
        self.markers = []
        self.last_char = ""
        self.at_line_start = False
        self.nonterminal = False
        self.finished = False

    def _visible(self, text):
        if text:
            self.last_char = text[-1]
            if self.markers and text.strip():
                self.nonterminal = True
        return text

    def feed(self, chunk):
        text = self.pending + str(chunk)
        self.pending = ""
        output = []
        while text:
            if self.marker is not None:
                end = next((i for i, c in enumerate(text) if c in "〕\r\n"), None)
                part = text if end is None else text[:end + 1]
                self.marker = (self.marker + part)[:self.MAX_MARKER_CHARS + 1]
                if end is None:
                    break
                self.markers.append((self.marker, self.at_line_start))
                self.marker = None
                text = text[end + 1:]
                continue
            start = text.find(self.PREFIX)
            if start >= 0:
                output.append(self._visible(text[:start]))
                self.at_line_start = not self.last_char or self.last_char in "\r\n"
                self.marker = self.PREFIX
                text = text[start + len(self.PREFIX):]
                continue
            hold = max((n for n in range(1, len(self.PREFIX))
                        if text.endswith(self.PREFIX[:n])), default=0)
            output.append(self._visible(text[:-hold] if hold else text))
            self.pending = text[-hold:] if hold else ""
            break
        return "".join(output)

    def finish(self):
        # 不把被截断的标记或半截前缀漏给用户。
        if self.marker is not None or self.pending:
            self.markers.append((self.marker or self.pending, False))
        self.pending = ""
        self.marker = None
        self.finished = True

    @property
    def status(self):
        if not self.markers:
            return "missing"
        return "valid" if self.numbers is not None else "invalid"

    @property
    def numbers(self):
        if len(self.markers) != 1 or self.nonterminal:
            return None
        text, line_start = self.markers[0]
        match = re.fullmatch(r"〔依据:([0-9]+(?:,[0-9]+)*)〕", text)
        if not line_start or not match:
            return None
        numbers = list(dict.fromkeys(int(n) for n in match[1].split(",")))
        valid = [n for n in numbers if 1 <= n <= self.candidate_count]
        return valid or None

    def stream(self, chunks):
        for chunk in chunks:
            visible = self.feed(chunk)
            if visible:
                yield visible
        self.finish()


def strip(text, candidate_count):
    parser = MarkerFilter(candidate_count)
    visible = parser.feed(text)
    parser.finish()
    return visible, parser
