import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from core.htmlblocks import html_to_blocks


def test_headings_and_paragraphs():
    html = "<h2>第一节</h2><p>段落一</p><p>段落二</p><h2>第二节</h2><ul><li>要点</li></ul>"
    blocks, images = html_to_blocks(html)
    assert [b["heading"] for b in blocks] == ["第一节", "第二节"]
    assert blocks[0]["text"] == "段落一\n\n段落二"
    assert blocks[1]["text"] == "要点"
    assert images == []


def test_preamble_before_first_heading():
    blocks, _ = html_to_blocks("<p>引言</p><h2>正文</h2><p>内容</p>")
    assert blocks[0]["heading"] == "" and blocks[0]["text"] == "引言"


def test_formula_becomes_latex():
    html = '<p>质能方程 <img src="https://www.zhihu.com/equation?tex=E%3Dmc%5E2" alt="E=mc^2" class="eeimg"></p>'
    blocks, images = html_to_blocks(html)
    assert "$E=mc^2$" in blocks[0]["text"] and images == []


def test_images_collected_and_normalized():
    html = ('<figure><img data-original="//pic1.zhimg.com/v2-abc_b.jpg" '
            'src="//pic1.zhimg.com/v2-abc_b.jpg"><figcaption>图1</figcaption></figure>'
            '<p><img src="https://pic2.zhimg.com/v2-def.png"></p>')
    blocks, images = html_to_blocks(html)
    assert images == ["https://pic1.zhimg.com/v2-abc.jpg", "https://pic2.zhimg.com/v2-def.png"]
    assert "图1" in blocks[0]["text"]


def test_skip_script_and_noscript():
    blocks, images = html_to_blocks('<script>var x=1</script><noscript><img src="http://x/1.jpg"></noscript><p>正文</p>')
    assert images == [] and blocks[0]["text"] == "正文"


def test_entities_and_br():
    blocks, _ = html_to_blocks("<p>a&amp;b<br>c</p>")
    assert blocks[0]["text"] == "a&b\nc"


def test_lazy_data_src_images():
    html = ('<p>正文</p><img class="rich_pages" data-ratio="0.7" '
            'data-src="https://mmbiz.qpic.cn/mmbiz_png/abc/640?wx_fmt=png" />')
    blocks, images = html_to_blocks(html)
    assert images == ["https://mmbiz.qpic.cn/mmbiz_png/abc/640"]
    assert blocks[0]["text"] == "正文"
