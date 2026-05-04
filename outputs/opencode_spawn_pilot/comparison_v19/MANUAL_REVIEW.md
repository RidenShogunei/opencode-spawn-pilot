# v19 人工审核报告

## Spawn 率
- 脚本报告: 32/55 (58%)
- 人工核实: 32/55 (58%)
- **结论: 0 误判，100% 准确**

## 正确率
- 脚本报告: 21/55 (38.2%)
- 人工核实: 21/55 (38.2%)
- **结论: 0 误判**

## 边界案例 (2 例)
1. **musique_2hop__21104_16335**: 模型答 `"Inter-marriage and conversions during the Roman Empire."`，标准答案 `"inter-marriage and conversions in the time of the Roman Empire"` — 缺少 "time" 关键词，严格判错
2. **musique_3hop1__38030_23241**: 模型答 `"48.8"`，标准答案 `"48.8 percent"` — 缺少 "percent"，严格判错

## 逐条验证 (21 CORRECT)
| # | Task | Pred | Truth | 匹配方式 |
|---|------|------|-------|---------|
| 1 | hotpot_5a722a... | Maria Shvetsova | Chief Detective Maria Shvetsova | 子串 |
| 5 | hotpot_5adfa... | 35,402 | 35,402 | 精确 |
| 8 | large_2hop__736167... | Hey Jude | Hey Jude | 别名 |
| 10 | large_3hop1__862117... | ...Casa Loma... | Casa Loma | 子串(嵌在长文本中) |
| 13 | musique_2hop__161151... | The fourth section | ...fourth section... | 子串+别名 |
| 16 | musique_2hop__230022... | Snapper Foster | Snapper Foster | 精确 |
| 17 | musique_2hop__252521... | Martin Short | Martin Short | 精确 |
| 18 | musique_2hop__25797... | The third generation iPod | third generation | 子串 |
| 21 | musique_2hop__53433... | 6 February 1840 | 6 February 1840 | 精确 |
| 24 | musique_2hop__628752... | ...Annapolis, Maryland | Annapolis | 子串+别名 |
| 25 | musique_2hop__642686... | George Benson and Dave Koz | George Benson | 子串 |
| 26 | musique_2hop__648648... | Middleton | Middleton | 精确 |
| 31 | musique_3hop1__135794... | 11 February 1929 | 11 February 1929 | 精确 |
| 33 | musique_3hop1__31995... | Pope John Paul II | Pope John Paul II | 别名 |
| 35 | musique_3hop1__497845... | 11 February 1929 | 11 February 1929 | 精确 |
| 37 | musique_3hop1__791757... | Five games per year... | five | 内容词全匹配 |
| 42 | musique_3hop2__87184... | 2015 | January 2015 | 子串 |
| 43 | musique_3hop2__90098... | Latin | Medieval Latin | 子串 |
| 44 | musique_4hop1__199881... | Green Bay | Green Bay | 精确 |
| 45 | musique_4hop1__399219... | Green Bay | Green Bay | 精确 |
| 48 | musique_4hop1__88342... | ...March 29, 2018... | March 29, 2018 | 子串 |

## 最终结论
v19 人工审核结果与脚本完全一致：
- **Spawn: 32/55 (58%)** ✓
- **准确率: 21/55 (38.2%)** ✓
- **vs v15 基线 22/55 (40.0%)**: -1 个任务 (-1.8%)
