# Auto-List-Products

把一堆商品截图，变成商城里「已填好、已校验」的商品。

一个**平台无关**的商品批量上架 skill：agent 先探测你自己的后台（必填项、类目树、默认值、校验点），
生成一份 profile 让你确认，然后按队列逐单填写、逐项校验、按授权提交。

没有写死任何后台、类目树或业务规则——换平台只是重新探测一次。

## 它做什么

| 阶段 | 产出 | 工具 |
|---|---|---|
| 环境自检 | 确认浏览器通道、真实输入能力、依赖、登录态、提交模式 | 无（agent 按 `SKILL.md` 执行） |
| 探测 | `profile.json`：字段定位符、上传槽（商品图/详情图/规格图）、类目树、默认值、校验点、业务规则 | `check_profile.py` |
| 收图 | 方图；白图不落盘（自动改用备图重裁），轮播圆点自动裁掉并补白 | `crop_square.py` |
| 体检 | 本地或线上图片的白图检测（字节数 + 颜色数，按内容不按扩展名） | `check_images.py` |
| 建队列 | 可断点续跑的 `queue.json`，规则全部取自 profile，同名同价自动去重 | `make_queue.py` |
| 登记表 | 续填 Excel：自动定位空行、供货价保持数字、不打断公式、继承上一行样式 | `fill_xlsx.py` |
| 填写 | 上传→名称→类目→范围→规格→SKU→逐项校验→提交 | `references/browser-playbook.md` |
| 对账 | 抓取商品列表快照 → 计划 vs 店里：缺失/重复/价格不符/规格结构不符/状态未生效，离线只读 | `reconcile.py` |

## 安装

```bash
npx skills add https://github.com/TheChosenOne666/Auto-ListProducts-skill --skill "auto-list-products"
```

或把 `skills/auto-list-products/` 拷进你的 agent skill 目录（`~/.qoder-cn/skills/`、`~/.claude/skills/`
或项目的 `.qoder/skills/`），reload 后即可 `/auto-list-products` 调起。

```bash
pip install pillow openpyxl   # 唯一的依赖
```

## 一次典型运行

```
1  预检        没有浏览器连接器 → 引导装扩展 → 确认是同一个已登录会话
2  探测        打开上架页 → 读必填项 / 上传槽 / 类目树 / 默认值 / 校验点 → 问业务规则 → 写 profile.json
3  试点        只跑一个商品，全部断言通过
4  批量        裁图 → 提参数 → 建队列 → 逐单填写、逐项校验、按授权提交
5  对账        抓商品列表快照 → 计划 vs 店里四类差异 → 按清单补漏/纠错，退出码非 0 就不算跑完
```

## 仓库结构

```
skills/auto-list-products/
├── SKILL.md                       主流程：预检 → 探测 → 裁图 → 队列 → 填写 → 对账
├── README.md                      这个 skill 的详细说明
├── profile.example.json           探测产出的平台档案
├── listings.example.json          商品列表快照的形状（对账的输入）
├── references/                    探测清单、浏览器手法、踩坑清单
├── scripts/                       六个可执行脚本（零 SDK 依赖）
└── evals/evals.json               验收用例：带 skill / 不带 skill 对比
tests/smoke.py                     端到端冒烟测试：造夹具→跑六个脚本→断言产物
.github/workflows/check-skills.yml 结构自检 + 冒烟测试，Linux 与 Windows 各跑一遍
```

## 为什么是 skill，不是平台

平台要托管别人的账号、凭据、任务和计费，合规与运维都落在维护者身上；而这类工作的凭据本来就该留在
用户自己的浏览器里。skill 一个仓库就能分发，平台的差异交给「首次探测」解决。

## 安全边界

- 不上传、不提交，除非用户明确授权；每个商品提交前逐项校验。
- 不处理账号密码：掉登录就把标签交还给用户。
- 不绕过平台审核、风控或验证码，不用页面脚本伪造上传。

## License

MIT
