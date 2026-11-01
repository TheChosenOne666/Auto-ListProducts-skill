# Auto List Products

把一堆商品截图，变成商城里「已填好、已校验」的商品。平台无关、策略无关：agent 先探测你的后台生成一份 profile，业务规则（积分怎么算、要不要跳过、显示数填不填）由你在探测时决定。

A platform-agnostic skill that turns product screenshots into verified listing submissions:
square crops with blank/artifact guards, fact extraction, a registration spreadsheet that keeps its
formulas alive, a resumable queue, and browser form filling that refuses to submit bad data.

## 能力

| 阶段 | 做什么 | 脚本 |
|---|---|---|
| 探测 | 进用户自己的后台，摸清表单/类目树/默认值/校验点，生成 profile | `scripts/check_profile.py` |
| 收图 | 截图裁成方图，自动识别白图、轮播圆点并修正 | `scripts/crop_square.py` |
| 体检 | 本地或线上图片的白图检测（字节数 + 颜色数） | `scripts/check_images.py` |
| 建队列 | 商品参数 → 可断点续跑的上架队列（送券/整箱价/积分/显示数均为可选） | `scripts/make_queue.py` |
| 登记表 | 续填 Excel，供货价保持数字、箱规用格式显示，不打断公式 | `scripts/fill_xlsx.py` |
| 浏览器 | 上传、选类目、规格矩阵、SKU、逐项校验后提交 | `references/browser-playbook.md` |

## 快速开始

```bash
pip install pillow openpyxl

# 1. 裁方图（先看 contact sheet，再处理 blank 报告）
python scripts/crop_square.py --src shots --dst images --products work/products.json \
    --tops "1440x3200:285,1280x2844:245" --sheet work/crops.png --report work/crop-report.json

# 2. 图片体检
python scripts/check_images.py --dir images

# 3. 生成上架队列（--root 必填；--min-coupon 是可选的跳过阈值，默认不跳）
python scripts/make_queue.py --products work/products.json --categories work/categories.json \
    --root "根类目名" --out work/queue.json

# 4. 续填登记表
python scripts/fill_xlsx.py --xlsx 登记表.xlsx --sheet 登记表 --rows work/rows.json --start 52
```

浏览器那一步由 agent 按 `SKILL.md` 与 `references/` 执行。**新平台第一次先跑探测**：
agent 进你的后台打开上架页，摸清字段、类目树、默认值和校验点，写成 `profile.json` 给你确认；
之后同一平台直接复用，不用再探。

## 安全边界

- 不上传、不提交，除非用户明确授权；每个商品提交前逐项校验。
- 不处理账号密码：掉登录就把标签交还给用户。
- 不绕过平台审核、风控或验证码。

## 目录

```
SKILL.md                       主流程：环境自检 → 探测 → 收图 → 队列 → 填写 → 审计
profile.example.json           探测产出的平台档案示例
scripts/                       可执行脚本（无第三方 SDK 依赖）
references/                    探测清单、浏览器手法、踩坑清单
```
