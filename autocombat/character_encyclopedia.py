"""角色图鉴唯一文案区。数值来自 chData，机制说明以当前战斗实现为准。

展示资料不参与战斗；不要把平衡参数写进这里。技能按编号和阶段匹配，
同名技能（例如木落的两种召唤）保留独立条目。
"""

# 编号：徽记、配色、两个定位词及各自在该角色上的含义。
PROFILES = {
 'Y1': ('flame', '#db775a', [('附魔', '火焰攻击命中后留下持续灼烧。'), ('强攻', '奖励骰提高攻击检定的可靠性。')]),
 'Y2': ('meteor', '#b07958', [('连击', '一次火球术分别结算三段攻击。'), ('召唤', '使魔提供独立行动与战场人数。')]),
 'Y3': ('horn', '#5b9b91', [('近战', '依靠普通攻击和反击输出。'), ('强化', '常驻攻击奖励骰与附加伤害骰。')]),
 'Y4': ('microphone', '#c88c45', [('双击', '星闪攻击分别结算两段伤害。'), ('干扰', '演唱使敌方攻击检定承受惩罚骰。')]),
 'Y5': ('bat', '#c56785', [('吸血', '以造成的伤害回复自身生命。'), ('机动', '飞行被动提高行动力与攻击可靠性。')]),
 'Y6': ('snow', '#6694bc', [('冰冻', '雪球、雪带与小雪人共同累积八层循环。'), ('守护', '雪幕减伤、法杖护盾及冰冻回复维持生存。')]),
 'Y60': ('snowman', '#84a9b2', [('领域', '旧版雪带以固定区域削弱敌人。'), ('减伤', '随身雪幕保护范围内友军。')]),
 'Y7': ('flower', '#9a80b1', [('战意', '逐轮压制敌方主体并迫使召唤物撤离。'), ('救援', '治疗领域可急救，返生之花可复活近期阵亡者。')]),
 'Y8': ('cake', '#cb8b9e', [('制造', '将魔力转为可保存、食用与递交的蛋糕。'), ('补给', '以恢复和进攻类蛋糕分配队伍资源。')]),
 'Y9': ('branch', '#658878', [('变身', '生命降低后可能由木·荣转入木·枯。'), ('生灭', '生阶段治疗与召唤，灭阶段以持续伤害换取进攻。')]),
 'Y10': ('hummingbird', '#c29b52', [('守护', '群体屏障与治疗保护队伍。'), ('召唤', '藤蔓和蜂鸟提供地面与空中支援。')]),
 'Y11': ('scythe', '#688ab6', [('汲取', '镰刀的生命吸取帮助近战续航。'), ('黑雾', '移动领域削弱闪避，并在进入时概率抽取魔力。')]),
 'Y12': ('portal', '#847fc0', [('空间', '扭曲削弱敌人，扩张强化友军。'), ('引入', '从三种异界召唤物中抽取，并可形成三合一。')]),
 'Y13': ('giant', '#a07b59', [('飞行', '常驻飞行与额外行动力。'), ('魔躯', '魔力消耗减免与生命损耗转魔力共同构成防御。')]),
 'Y14': ('cartridge', '#ab7fbd', [('卡带', '三种形态分别提供射击、重剑与高速救援。'), ('蓄势', '极限槽和金环由战斗事件累积，切换时按形态管理。')]),
 'Y15': ('cloak', '#c26742', [('爆破', '受击反应、连发火焰与火焰领域形成爆发。'), ('火衣', '火鼠裘按伤害分档消耗魔力减伤。')]),
 'Y16': ('quill', '#6686b6', [('幻造', '写作检定改变攻击附加伤害，武器决定技能组合。'), ('月能', '虚假之月储存能量、承接溢出回复并支撑终结技。')]),
 'Y17': ('bloodmoon', '#ae566e', [('血术', '以储血、献血与魔力支付不同技能的代价。'), ('塑兵', '血刃、血弓和长枪提供不同射程与消耗方式。')]),
}

# 每名参赛者的逐技能机制正文；通用的费用、范围、检定、持续、模板信息
# 由下方函数从实时 chData 补入，避免平衡修改后图鉴仍显示旧数字。
# 稳定键：(参赛者编号、技能名、阶段)。正文不依赖技能排列顺序。
ARTICLES = {('Y2', '火球术', 0): '向同一目标连续发射{count}枚火球，每枚分别以 {values} 的技能值检定，依次造成 {damage} 伤害。',
 ('Y2', '召唤使魔', 0): '召唤 {count} 只使魔加入战斗{cap}。名额已满时无法施放，使魔死亡后可以补召。',
 ('Y2', '速攻', 0): '战斗开始时支付费用，获得 {gain}，{duration}。魔力不足时无法发动。',
 ('Y3', '斗殴强化', 0): '战斗开始时获得 {reward}，适用于普通攻击、法术攻击及反击，{duration}。',
 ('Y3', '附魔术', 0): '战斗开始时支付费用，为自身的普通攻击及法术攻击附加 {damage} 伤害，{duration}。魔力不足时无法发动。',
 ('Y4', '星闪攻击', 0): '向同一目标连续发动{count}段星闪攻击，每段分别以 {values} 的技能值检定，各获得 {reward}，依次造成 {damage} 伤害。',
 ('Y4', '演唱', 0): '使敌方全体获得 {penalty}，适用于普通攻击、法术攻击及反击，持续 {duration} 轮。',
 ('Y5', '飞行', 0): '提高行动力并赋予攻击奖励骰。实际飞行资格同时遵循角色属性和飞行状态规则。',
 ('Y5', '吸血', 0): '近距离血术攻击允许反击、不允许闪避。有效伤害大于零时按修正伤害吸取生命，最终回复受生命上限约束；吸血基数和护盾的先后关系见特殊规则。',
 ('Y6', '制造雪带', 0): '创建固定雪带，创建动作不立即增加冰冻值；随后每轮向区域内敌人施加冰冻。自身同类雪带只能同时存在一处。',
 ('Y6', '雪幕', 0): '以自身为中心移动的保护领域，减少友方受到的伤害；来源死亡后解除。',
 ('Y6', '发送雪球', 0): '连续投出两个雪球，每段命中分别增加冰冻值；每段可独立闪避。',
 ('Y6', '雪之法杖', 0): '入场时向己方授予攻击附魔与护盾。伤害骰加值持续5轮，到期失效且不自动刷新；护盾保留默认延续设置，耗尽后消失。',
 ('Y6', '堆雪人', 0): '召唤小雪人。小雪人可施放自身的小雪带与雪球，冰冻记在主人名下；它的领域与主人的领域分别占用上限。',
 ('Y60', '制造雪带', 0): '旧版雪带包含固定减速、斗殴削减和持续伤害，并额外发动一次群体攻击；不使用 v2 的冰冻层数体系。',
 ('Y60', '雪幕', 0): '跟随自身的友方减伤领域；施法者死亡后解除。',
 ('Y60', '发送雪球', 0): '投出一枚雪球，进行一次可闪避、不可反击的远程攻击。',
 ('Y7', '战意值削弱', 0): '战斗开始时获得战意光环，逐轮削弱敌方主体与召唤物的战意。主体承受攻击惩罚、伤害降低及沉默；召唤物战意归零时整组撤离。',
 ('Y7', '治愈领域', 0): '创建随自身移动的治愈领域，半径 {radius} 格，持续 {duration} 回合。范围内的自身、己方主体及召唤物分别回复 {hp} HP 与 {mp} MP。',
 ('Y7', '返生之花', 0): '复活 1 个己方阵亡主体或召唤物，生命恢复至上限的 {hp_ratio}，向下取整，最低为 1 HP。{uses}，施放后冷却 {cooldown} 回合。',
 ('Y8', '美味的蛋糕', 0): '立即制造 {count} 个{item}，放入己方共享制造物池。每个{benefit}',
 ('Y8', '甜甜的蛋糕', 0): '立即制造 {count} 个{item}，放入己方共享制造物池。每个{benefit}',
 ('Y8', '品尝蛋糕', 0): '战斗开始时回复自身 {hp} HP。附带品尝回复的生命蛋糕在食用时，额外为食用者回复 {extra} HP。',
 ('Y8', '最后的款待-恢复', 0): '立即制造并分发{items}，两类蛋糕分别放入己方主体的个人库存。',
 ('Y8', '轻盈蛋糕', 0): '立即制造 {count} 个{item}，放入己方共享制造物池。{benefit}',
 ('Y8', '幸运蛋糕', 0): '立即制造 {count} 个{item}，放入己方共享制造物池。{benefit}',
 ('Y8', '最后的款待-进攻', 0): '立即制造并分发{items}，两类蛋糕分别放入己方主体的个人库存。',
 ('Y9', '召唤生灵', 1): '木·荣召唤成群生灵；魔力费用随实际召唤数量变化。',
 ('Y9', '生之矛', 1): '可攻击敌人，也可对友方先伤害再登记分轮回复。敌我分支并不对称，实际代码中的额外回复挂接位置与字段名称存在差异；须结合结算步骤与边界说明阅读。',
 ('Y9', '领域·生', 1): '创建随身友方治疗领域，并给予限时敏捷提升。',
 ('Y9', '阶段转换', 1): '木·荣受到生命阈值检查时按阈值概率转为木·枯，支付理智代价。生命越低，转阶段越可靠。',
 ('Y9', '召唤生灵', 2): '木·枯召唤生灵，并启用点燃相关伤害效果；召唤数量决定费用。',
 ('Y9', '死之矛', 2): '攻击敌人并按伤害的一部分吸取生命，不再使用生之矛的敌我延迟治疗分支。',
 ('Y9', '领域·灭', 2): '持续对范围内所有阵营造成伤害，同时强化己方敏捷、伤害和攻击检定。己方也会被领域灼伤。',
 ('Y10', '屏障术', 0): '为范围内己方授予独立护盾，护盾耗尽或持续结束后失效。',
 ('Y10', '治疗术', 0): '即时恢复范围内己方生命，不能超过各自生命上限。',
 ('Y10', '藤蔓术', 0): '召唤藤蔓。藤蔓自带禁锢，不能像普通单位自由移动。',
 ('Y10', '召唤蜂鸟', 0): '召唤飞行蜂鸟，数量由骰子决定并受召唤上限约束。',
 ('Y11', '挥动镰刀', 0): '镰刀攻击附带生命吸取，并对本次防御反应施加惩罚骰；此外向敌方施加限时致死骰劣势。',
 ('Y11', '黑雾笼罩', 0): '创建随身黑雾：区域内敌人闪避降低，进入时另有概率追加闪避削弱并抽取魔力。自身同时获得攻击奖励骰和更长时间的行动力提升。',
 ('Y11', '镰刃余势', 0): '常驻附加伤害，随攻击伤害结算。',
 ('Y12', '空间扭曲', 0): '对敌方施加限时攻击惩罚骰。',
 ('Y12', '空间压缩', 0): '压缩空间攻击单个敌人；不能闪避、不能反击，且伤害不能贯穿。',
 ('Y12', '空间扩张', 0): '对己方施加限时攻击奖励骰。',
 ('Y12', '空间引入', 0): '从玄武、朱雀、龙虎中抽取召唤物，同一施法者按唯一抽取规则管理。三类齐备时按合体规则形成三合一；并非从所有召唤模板任意抽取。',
 ('Y13', '魔力永驻', 0): '将魔力消耗百分比降低到零；按引擎的费用下限规则结算，不应把所有正费用技能理解为绝对免费。',
 ('Y13', '飞行', 0): '常驻飞行并提高自身行动力。地面单位近战交互遵循飞行规则。',
 ('Y13', '斗殴精通', 0): '常驻攻击奖励骰。',
 ('Y13', '魔力护体', 0): '把生命损耗转为魔力消耗；魔力不足时仍受实际结算规则限制。',
 ('Y1', '斗殴精通', 0): '战斗开始时获得 {reward}，适用于普通攻击、法术攻击及反击，{duration}。',
 ('Y1', '火焰附魔', 0): '发动一次火焰攻击，以{skill}检定，造成 {damage} 伤害。目标可以{reactions}。',
 ('Y14', '卡带切换·魂斗罗', 0): '切换至魂斗罗，启用光枪攻击与残机替身。切换共享冷却；已弹出的卡带本场不能再次加载。',
 ('Y14', '卡带切换·最终幻想', 0): '切换至最终幻想，默认进入迅捷模式，并启用极限槽。',
 ('Y14', '卡带切换·音速索尼克', 0): '切换至音速索尼克，启用金环与高速救援技能。',
 ('Y14', '像素射击', 1): '魂斗罗形态的基础光枪攻击，同时供该形态普通攻击读取。',
 ('Y14', 'S弹幕·散射', 1): '依次结算三段散射攻击；当前实现按三条攻击效果执行。',
 ('Y14', 'S弹幕·集中', 1): '集中弹幕形成一段强化攻击。',
 ('Y14', 'L型贯穿激光', 1): '发动不可闪避、不可反击的贯穿激光；仍需要通过攻击检定。',
 ('Y14', '残机替身', 1): '致命伤到来时，若魔力足够则支付费用，在邻近合法位置以少量生命重生；弹出魂斗罗。每次变身限一次，使用后本场不能重新加载该卡带。',
 ('Y14', '重剑·迅捷', 2): '迅捷模式的重剑攻击，同时决定该模式普通攻击的伤害。',
 ('Y14', '重剑·勇气', 2): '勇气模式的重剑攻击，伤害高于迅捷模式。',
 ('Y14', '模式切换·迅捷', 2): '切为迅捷模式，替换原模式增益，获得行动力与攻击奖励骰。',
 ('Y14', '模式切换·勇气', 2): '切为勇气模式，替换原模式增益，降低行动力并获得伤害骰优势。',
 ('Y14', '极限槽', 2): '最终幻想的专属资源。造成实际敌方生命损失、受到实际伤害、勇气模式成功反击分别按每轮事件上限蓄积；其他己方主体首次濒死另有奖励。',
 ('Y14', '三连斩·散', 2): '三段重剑攻击分别检定，伤害骰各不相同。',
 ('Y14', '三连斩·聚', 2): '把三连斩集中为一段不可闪避、不可反击的攻击。',
 ('Y14', '极限技·凶斩', 2): '使用极限槽发动高伤害斩击。除了魔力，还须满足并支付极限槽要求；每场限一次。',
 ('Y14', '金环', 3): '索尼克专属资源。成功闪避或一次冲刺中累计移动到要求距离可获得金环，同一轮的这两类事件共享一次获取。',
 ('Y14', '回旋踢', 3): '索尼克形态的近战攻击，也供该形态普通攻击读取。',
 ('Y14', '音速冲刺', 3): '临时提高行动力、获得攻击奖励骰并进入飞行；冲刺实际移动满足距离后可触发金环。',
 ('Y14', '光速救援', 3): '先调用向所选友方坐标传送的逻辑，再为预先解析的范围目标授予护盾。当前没有相邻安全落点搜索，不能直接视为完整的救援位移规则。',
 ('Y14', '金环回收·壹', 3): '消耗一枚金环恢复自身生命。',
 ('Y14', '金环回收·贰', 3): '消耗两枚金环恢复自身生命，并额外获得限时护盾。',
 ('Y16', '灵感大爆发', 0): '为己方施加可叠加的灵感效果。发生写作检定等灵感事件后，在该单位回合结算魔力返还；每轮每单位只结算一次，至多计入两层。',
 ('Y16', '虚假之月', 0): '召唤虚假之月并建立己方检定增益链接。月亮存活、链接有效时可承接溢出回复为月能；月能不等同于施法者自身魔力。',
 ('Y16', '月中的倒影', 0): '召唤两个限时影之克隆参与战斗。召唤物使用主人的幻造加成时按所属关系读取写作。',
 ('Y16', '辉月女神的祝福', 0): '为己方提供一次伤害免疫；生命阈值触发的追加庇护还须支付月能。',
 ('Y16', '幻造兵武', 0): '攻击得到固定附魔，另外进行写作检定按成功等级增加或扣减伤害。普通攻击、幻造法术使用同一写作收益表。',
 ('Y16', '幻造兵武·即兴短句', 0): '短句攻击可充能月亮。法杖额外充能；弓形态增加费用并改为三连射，附加弓伤害，整次连射共享一次写作结果且只充能一次。',
 ('Y16', '切换·法杖', 0): '切为法杖，支持群体护盾并强化短句充能。不能重复切到已装备的武器。',
 ('Y16', '切换·弓', 0): '切为弓，支持连射短句与吟唱攻击。',
 ('Y16', '切换·剑', 0): '切为剑，支持消耗月亮的终结攻击。',
 ('Y16', '万众瞩目的乌托邦', 0): '弓专属吟唱攻击，吟唱完成后发动贯穿幻造伤害，写作检定继续影响伤害。',
 ('Y16', '已然遥远的理想城', 0): '法杖专属群体护盾，吸收的伤害按比例转为月能。',
 ('Y16', '月光王座全额率输出', 0): '剑专属终结技。需要存活且月能超过门槛的虚假之月；消耗月亮，以月能倍率形成伤害。占位伤害骰不是实际伤害。',
 ('Y15', '爆破圈', 0): '受攻击时触发的反应技，每场限一次。发动爆破并附加持续灼烧，不占普通主动技的使用入口。',
 ('Y15', '瞬爆', 0): '发动火焰爆击，命中后留下数轮灼烧。',
 ('Y15', '余烬', 0): '一次动作发动三段火焰攻击，分别检定并结算。',
 ('Y15', '燃梦孤灯·自身', 99): '保留的非战斗技能：恢复自身生命与理智，当前战斗阶段不开放。',
 ('Y15', '燃梦孤灯·他人', 99): '保留的非战斗技能：恢复附近其他友方的生命与理智，当前战斗阶段不开放。',
 ('Y15', '葬火流光', 0): '先创建随身火焰领域，再发动一次敌方群体直接攻击。领域在后续轮次继续造成伤害，直接攻击与领域各自按声明顺序结算。',
 ('Y15', '火鼠裘', 0): '每次受击在护盾前分档减伤，魔力不足时不能启动。具体档位见角色被动规则。',
 ('Y15', '焰动力·悬浮', 0): '常驻飞行，同时降低自身行动力。',
 ('Y15', '焰动力·疾飞', 0): '提高自身及附近其他友方的行动力，自身额外获得攻击奖励骰，持续一轮。',
 ('Y17', '猩红眼眸', 99): '原设定中的鲜血感知技能，保留在资料中；当前设为非战斗阶段，不能作为战斗主动技使用。',
 ('Y17', '阿瑞卡纳律动', 0): '以魔力、储血与必要的生命反噬换取定向范围攻击。起手锁定来源与方向，之后须用主动作续唱；实际扣蓝、有效伤害投入、反噬及耗空魔力分支分别计算。',
 ('Y17', '凝血式-守', 0): '按血液投入生成限时固定减伤，有上限；储血不足时允许献出当前生命的一半换取护甲，AI不主动采用献血防御。',
 ('Y17', '祭炼式-破', 0): '吟唱完成后令自身进入濒死，对目标造成基于施法者最大生命的伤害；自身随后死亡还会追加基于目标最大生命的伤害。吟唱中实际生命损失可打断，并结算重伤、生命及魔力惩罚。',
 ('Y17', '凝血式-幻', 0): '消耗血液塑成限时血刃或血弓。储血不足时按生命换血比例补足，但献血不得超过当前生命一半；同形态仍有效时不能重复塑造。',
 ('Y17', '污血战争', 0): '以自身为中心无差别冲击，实际包含己方。消耗魔力、生命和理智；额外魔力扩大范围，击杀回复生命和理智并收集尸血。血液达到连锁门槛后可再次发动冲击。',
 ('Y17', '凝血式-存', 0): '消耗整份血量直接治疗范围内合格的轻伤友方。当前实现不会先制造可存储的血球物品；治疗量由份数和血术规则决定，拒绝无效目标或过量投入。',
 ('Y17', '凝血式-浮', 99): '原设定中的血液飞行，当前保留为非战斗阶段技能。',
 ('Y17', '等价交换', 99): '原设定中的物质换血，当前保留为非战斗阶段技能；没有实现任意物体的战斗转化。',
 ('Y17', '血染披风', 0): '按整份血液换回魔力，投入不得超过当前缺失魔力所需；不会凭空获得无限魔力。',
 ('Y17', '血月斩击', 0): '持血刃发动月相斩击；血液投入决定段数，月相决定伤害与吸取分支。命中收血上限在整次施放的各段之间共享。',
 ('Y17', '血色突刺', 0): '可凝聚或投掷血枪。每支枪有独立耐久和数量上限；普通攻击、反击或成功格挡会损耗耐久，投掷使用专属伤害和射程。持枪时优先使用长枪攻击档案。'}

ARTICLE_ORDER = {'Y2': [('火球术', 0), ('召唤使魔', 0), ('速攻', 0)],
 'Y3': [('斗殴强化', 0), ('附魔术', 0)],
 'Y4': [('星闪攻击', 0), ('演唱', 0)],
 'Y5': [('飞行', 0), ('吸血', 0)],
 'Y6': [('制造雪带', 0), ('雪幕', 0), ('发送雪球', 0), ('雪之法杖', 0), ('堆雪人', 0)],
 'Y60': [('制造雪带', 0), ('雪幕', 0), ('发送雪球', 0)],
 'Y7': [('战意值削弱', 0), ('治愈领域', 0), ('返生之花', 0)],
 'Y8': [('美味的蛋糕', 0), ('甜甜的蛋糕', 0), ('品尝蛋糕', 0), ('最后的款待-恢复', 0), ('轻盈蛋糕', 0), ('幸运蛋糕', 0), ('最后的款待-进攻', 0)],
 'Y9': [('召唤生灵', 1), ('生之矛', 1), ('领域·生', 1), ('阶段转换', 1), ('召唤生灵', 2), ('死之矛', 2), ('领域·灭', 2)],
 'Y10': [('屏障术', 0), ('治疗术', 0), ('藤蔓术', 0), ('召唤蜂鸟', 0)],
 'Y11': [('挥动镰刀', 0), ('黑雾笼罩', 0), ('镰刃余势', 0)],
 'Y12': [('空间扭曲', 0), ('空间压缩', 0), ('空间扩张', 0), ('空间引入', 0)],
 'Y13': [('魔力永驻', 0), ('飞行', 0), ('斗殴精通', 0), ('魔力护体', 0)],
 'Y1': [('斗殴精通', 0), ('火焰附魔', 0)],
 'Y14': [('卡带切换·魂斗罗', 0),
         ('卡带切换·最终幻想', 0),
         ('卡带切换·音速索尼克', 0),
         ('像素射击', 1),
         ('S弹幕·散射', 1),
         ('S弹幕·集中', 1),
         ('L型贯穿激光', 1),
         ('残机替身', 1),
         ('重剑·迅捷', 2),
         ('重剑·勇气', 2),
         ('模式切换·迅捷', 2),
         ('模式切换·勇气', 2),
         ('极限槽', 2),
         ('三连斩·散', 2),
         ('三连斩·聚', 2),
         ('极限技·凶斩', 2),
         ('金环', 3),
         ('回旋踢', 3),
         ('音速冲刺', 3),
         ('光速救援', 3),
         ('金环回收·壹', 3),
         ('金环回收·贰', 3)],
 'Y16': [('灵感大爆发', 0),
         ('虚假之月', 0),
         ('月中的倒影', 0),
         ('辉月女神的祝福', 0),
         ('幻造兵武', 0),
         ('幻造兵武·即兴短句', 0),
         ('切换·法杖', 0),
         ('切换·弓', 0),
         ('切换·剑', 0),
         ('万众瞩目的乌托邦', 0),
         ('已然遥远的理想城', 0),
         ('月光王座全额率输出', 0)],
 'Y15': [('爆破圈', 0),
         ('瞬爆', 0),
         ('余烬', 0),
         ('燃梦孤灯·自身', 99),
         ('燃梦孤灯·他人', 99),
         ('葬火流光', 0),
         ('火鼠裘', 0),
         ('焰动力·悬浮', 0),
         ('焰动力·疾飞', 0)],
 'Y17': [('猩红眼眸', 99),
         ('阿瑞卡纳律动', 0),
         ('凝血式-守', 0),
         ('祭炼式-破', 0),
         ('凝血式-幻', 0),
         ('污血战争', 0),
         ('凝血式-存', 0),
         ('凝血式-浮', 99),
         ('等价交换', 99),
         ('血染披风', 0),
         ('血月斩击', 0),
         ('血色突刺', 0)]}

# Below are display semantics, not combat parameters. The field inventory is
# deliberately explicit: new rule fields remain visible as unverified instead
# of silently disappearing or being assigned invented semantics.
import json
import re
from copy import deepcopy

VERSIONS = [
    {'id': 'encyclopedia', 'label': '百科版', 'description': '完整叙述机制，按前提、投入、效果和边界阅读。'},
    {'id': 'procedure', 'label': '流程版', 'description': '从怎么发动开始，依次阅读支付、检定与后续结算。'},
    {'id': 'reference', 'label': '速查版', 'description': '先看数值和限制，再查输入与机制；保留完整信息。'},
]
TARGETS = {'1': '自身', '2': '其他友方单体', '3': '其他友方全体（不含自身）',
           '4': '敌方单体', '5': '敌方全体', '12': '己方单体（可选自身）',
           '13': '己方全体（含自身）', '14': '自身与敌方单体', '15': '自身与敌方全体',
           '24': '友方或敌方单体（未选时优先敌方）', '25': '其他友方单体与敌方全体',
           '35': '区域内所有阵营（含自身）', '45': '敌方全体（所选敌人优先结算）',
           '124': '任意单体', '125': '己方单体与敌方全体', '135': '所有阵营（含自身）',
           'R': '随机敌方单位（可含召唤物）', 'RP': '随机敌方主体（不含召唤物）'}
TIMINGS = {'1': '被动', '2': '主动', '3': '附加动作', '4': '受击反应'}
PHASES = {'Y9': {1: '木·荣', 2: '木·枯'}, 'Y14': {1: '魂斗罗', 2: '最终幻想', 3: '音速索尼克'}}
WEAPONS = {'bow': '弓', 'staff': '法杖', 'sword': '剑'}
COUNTERS = {'rings': '金环', 'limit_gauge': '极限槽'}
EFFECT_TYPES = {1: '伤害', 2: '护盾', 3: '回复', 4: '增益或减益', 5: '召唤', 6: '制造',
                7: '延迟触发', 8: '领域', 9: '生命阈值触发', 10: '分轮回复', 11: '战意光环',
                12: '受伤修正', 13: '致命伤替身', 14: '资源计数器', 17: '阶段或武器切换',
                18: '友方传送', 19: '复活'}
DEFAULT = '未单独设定，依引擎默认规则'


def text_value(value):
    if value is None:
        return '未设限'  # Only used for explicitly declared null caps.
    if isinstance(value, bool):
        return '是' if value else '否'
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def quantity(value, unit=''):
    kind = '随机骰式' if isinstance(value, str) and re.search(r'\d+d\d+', value, re.I) else '固定值'
    return f'{text_value(value)}{(" " + unit) if unit else ""}（{kind}）'


def target_text(value):
    if value is None:
        return DEFAULT
    key = str(value).upper().strip()
    if key in TARGETS:
        return TARGETS[key]
    if '/' in key:
        return '；'.join(target_text(k) for k in key.split('/'))
    return f'未识别的目标码 {key}；不推断其作用对象'


def timing_text(value):
    key = str(value)
    return ' / '.join(TIMINGS.get(v, f'未识别时机 {v}') for v in dict.fromkeys(key))


def row(label, value, source=None):
    result = {'label': label, 'value': str(value)}
    if source:
        result['source'] = source
    return result


def percent(value):
    try:
        return f'{float(value):g}%'
    except (TypeError, ValueError):
        return text_value(value)


def ratio(value):
    try:
        return percent(float(value) * 100)
    except (TypeError, ValueError):
        return text_value(value)


# Complete semantic inventory of fields currently used by the contestants.
# Source keys are for auditing/tests; the interface presents the Chinese labels.
EFFECT_LABELS = {
 'type': '效果种类', '客体': '作用对象', '伤害骰': '基础伤害', '护盾值': '护盾容量',
 '回复hp': '生命回复', '回复mp': '魔力回复', '回复san': '理智回复',
 '射程': '射程', '作用半径': '作用半径', '持续回合': '持续时间', '成功率': '基础检定技能值',
 '默认延续性': '到期后延续',
 '成功率奖惩骰': '效果固有攻击奖惩骰', '可闪避性': '允许闪避', '可反击性': '允许反击',
 '可贯穿性': '允许贯穿伤害规则', '吸血比例': '生命吸取比例',
 '辅助效果': '辅助机制', '辅助效果值': '辅助机制数值', '技能加减值': '技能或属性修正',
 '属性削减': '区域属性削减', '每回合伤害骰': '每轮持续伤害', '冰冻每轮层数': '每轮冰冻增加',
 '领域中心跟随': '领域中心', '领域同时上限': '同来源同名领域上限',
 '来源存活绑定': '来源存活绑定', '领域急救': '领域允许急救濒死者',
 '领域立即结算': '施放时立即回复脉冲', '领域轮首结算次数': '后续轮首回复脉冲数',
 '复活主体': '允许复活主体', '复活召唤物主人政策': '复活召唤物的归属',
 '复活回复比例': '复活后生命比例', '死亡轮窗口': '允许的死亡轮窗口',
 '召唤物模板': '召唤对象', '召唤个数': '请求召唤数量',
 '制造物模板': '制造对象', '制造个数': '制造数量', '制造花费回合数': '制造等待',
 '制造分发': '分发方式', '分发人数覆盖': '指定队伍人数的分配', '分发削减顺序': '超额削减顺序',
 '分发给总数上限': '每种物品分发上限', '分发给总数公式': '每种物品总量公式',
 '分发自留': '默认自留份数', '分发队友每人': '默认每名队友份数',
 '消费追加回复hp': '食用额外生命回复', 'tasteBonusOwnerSerial': '品尝特性来源编号',
 '友方伤害骰': '对友方的基础伤害', '友方延迟回复公式': '友方后续回复公式',
 '友方延迟回复回合': '友方分轮回复时长', '友方延迟回复骰': '友方回复抽样骰',
 '友方行为': '友方分支', '敌方回复': '敌方后续回复公式',
 '敌方回复骰': '敌方回复抽样骰', '敌方延迟回复回合': '敌方分轮回复时长',
 'hp_thresholds': '生命阈值及触发概率', 'target_phase': '目标阶段',
 'ignite': '点燃召唤物', 'ignite_dmg_dice': '点燃后攻击伤害替换', 'ignite_tick_dmg': '点燃召唤物每轮自损',
 'on_enter_attr_debuff': '进入领域追加属性变化', 'on_enter_mp_drain_pct': '进入领域抽取当前魔力比例',
 'on_enter_trigger_rate': '进入领域触发概率', 'cooldown_rounds': '效果冷却',
 'eject_phases': '触发后永久弹出阶段', 'once_per_transform': '每次变身限一次',
 'respawn_hp': '替身重生生命', 'respawn_radius': '重生搜索半径',
 'counter_type': '资源种类', 'max_value': '资源上限', 'turn_mp_refund': '灵感回合返蓝',
 '可叠加': '叠加设置', 'linked_summon': '绑定召唤物', 'hp_threshold_grant': '低生命追加庇护',
 'bow_volley': '弓形态连射段数', 'charge_moon': '基础月能充能', 'staff_charge_bonus': '法杖额外月能充能',
 '幻造': '进行幻造写作检定', 'weapon_state': '切换到的武器',
 'shield_absorb_moon_ratio': '护盾吸收伤害转月能比例', 'consume_moon': '消耗月亮',
 'damage_moon_mult': '月能伤害倍率', 'variant': '受伤修正机制', '消耗mp_per_hit': '效果声明的每次受击魔力消耗',
 'damage_mp_multiplier': '投入魔力伤害倍率', 'damage_pct_caster_max_hp': '施法者最大生命伤害百分比',
 'damage_table': '动态伤害表',
 # Supported extensions from the documented loader, outside today's roster.
 '致死值': '致死检定值', '可反应性': '旧式防御反应开关', '忽略全局奖惩骰': '忽略施法者全局攻击奖惩骰',
 '技能名': '检定技能', '伤害骰优势': '伤害骰优势', 'damage_fixed': '固定伤害替换',
 'damage_pct_target_max_hp': '目标最大生命伤害百分比', 'damage_pct_caster_cur_hp': '施法者当前生命伤害百分比',
 'current_value': '初始资源', 'mod_delta': '资源改变量', '引发目标法术': '触发技能编号',
 '引发延迟回合': '触发延迟', '回复总量': '分轮回复总量', '消耗san': '效果触发理智费用',
 'zone_半径': '召唤物领域半径', 'zone_回复hp': '召唤物领域生命回复', 'zone_持续': '召唤物领域持续',
 'letter': '效果标记', 'exclude': '排除项', 'ally_safe': '己方安全设置',
}
SPELL_LABELS = {
 'name': '技能名称', 'category': '技能类别', 'timing': '使用时机', 'phase': '可用阶段',
 '消耗mp': '基础魔力消耗', '消耗san': '基础理智消耗', 'default_persist': '默认延续性',
 'cooldown_rounds': '技能冷却', '_once_per_battle': '每场限一次',
 '_mp_formula': '魔力公式', '_mp_formula_transformed': '变身后魔力费用替换',
 'weapon_required': '要求武器', '吟唱回合': '吟唱进度',
 'counter_required': '资源门槛与实际扣除', 'cost_counter_type': '支付资源种类',
 'cost_counter_amount': '支付资源数量', 'cost_blood_ml': '声明的基础血液费用',
 'blood_kind': '血术结算分支', 'mp_cost_formula': '血术魔力公式',
 'chant_interruptible': '允许中断吟唱', 'chant_interrupt_hp_pct': '中断时当前生命损失',
 'chant_interrupt_mp_pct': '中断时当前魔力损失', 'chain_on_blood_ml': '声明的连锁血液门槛',
 'cost_hp_dice': '生命消耗', 'cost_mp_pct_max': '最大魔力比例费用',
 'cost_mp_pct_current': '当前魔力比例费用', 'cost_hp_pct_current': '当前生命比例费用',
 'on_kill_heal_hp': '击杀生命回复', 'on_kill_heal_san': '击杀理智回复',
 'on_kill_blood_ml': '击杀收血', 'blood_per_hit_ml': '命中收血',
 'radius_per_extra_mp': '声明的追加魔力扩张系数', 'heal_limit_not_dying': '禁止对濒死者使用',
 'extra_attack_per_blood': '声明的额外斩击血液间隔', 'prep_free': '准备阶段免消耗',
 'charge_moon': '施放后月能充能', 'same_target_multiplier': '同目标伤害倍率',
 'blood_ml_per_hp': '血液生命换算', 'index': '技能编号',
}


def formatted_field(key, value, serial=''):
    if key in ('射程', '作用半径', 'respawn_radius', 'zone_半径'):
        return f'{value} 格' + ('（数据范围值，仍受地图和射程覆盖设置约束）' if key == '射程' else '')
    if key in ('持续回合', 'cooldown_rounds', '死亡轮窗口', '友方延迟回复回合', '敌方延迟回复回合', 'zone_持续'):
        return quantity(value, '轮')
    if key in ('可反击性', '可闪避性', '可贯穿性', '来源存活绑定', '领域急救', '复活主体', '可反应性', 'once_per_transform', '幻造', 'consume_moon', 'ignite', '忽略全局奖惩骰'):
        return '是' if value else '否'
    if key in ('吸血比例', '复活回复比例', 'on_enter_mp_drain_pct', 'on_enter_trigger_rate'):
        return ratio(value)
    if key in ('伤害骰', '护盾值', '回复hp', '回复mp', '回复san', '每回合伤害骰', '消费追加回复hp', '友方伤害骰', '友方延迟回复骰', '敌方回复骰', 'turn_mp_refund', 'ignite_dmg_dice', 'zone_回复hp'):
        return quantity(value)
    if key == 'type':
        return EFFECT_TYPES.get(value, f'未解释的效果类型 {value}')
    if key == '客体':
        return target_text(value)
    if key == '成功率奖惩骰':
        if isinstance(value, (int, float)):
            return f'{abs(value):g} 颗' + ('奖励骰' if value > 0 else '惩罚骰' if value < 0 else '额外奖惩骰')
        return {'b': '1颗奖励骰', 'p': '1颗惩罚骰'}.get(str(value), str(value))
    if key == '辅助效果值':
        return {'b': '1颗奖励骰', 'p': '1颗惩罚骰'}.get(str(value), text_value(value))
    if key == '领域中心跟随':
        return '随施法者移动' if value else '固定在创建位置'
    if key == 'hp_thresholds':
        return '；'.join(f"生命≤{ratio(v['ratio'])}：概率{percent(v['rate'])}" for v in value) + '；按当前满足的最高阈值检查一次，不累加概率'
    if key == 'hp_threshold_grant':
        return f"生命严格低于{ratio(value.get('ratio'))}、且当前无对应庇护时，消耗{value.get('cost_moon')}月能；辅助机制编号{value.get('aux_code')}"
    if key == 'target_phase':
        return PHASES.get(serial, {}).get(value, f'阶段 {value}')
    if key == 'eject_phases':
        return '、'.join(PHASES.get(serial, {}).get(v, str(v)) for v in value)
    if key in ('weapon_state', 'weapon_required'):
        return WEAPONS.get(value, f'未核实武器 {value}')
    if key == 'counter_type':
        return COUNTERS.get(value, str(value))
    if key == '复活召唤物主人政策':
        return {'caster': '召唤物候选仅限本施法者所有', 'any': '任意所有者'}.get(value, text_value(value))
    if key == '友方延迟回复公式':
        return {'max_double': 'max(友方实际生命损失×2，回复骰掷骰值)'}.get(value, f'未核实公式 {value}')
    if key == '敌方回复':
        return {'min_half': 'min(有效伤害÷2向下取整，回复骰掷骰值)；当前标准敌方分支未挂接此延迟回复，见边界说明'}.get(value, f'未核实公式 {value}')
    if key == '友方行为':
        return {'damage_and_heal': '先伤害，再登记分轮回复'}.get(value, text_value(value))
    if key == '分发人数覆盖':
        return '；'.join(f"{n}名主体：" + '、'.join(f"{EFFECT_LABELS.get(k,k)} {v}" for k,v in cfg.items()) for n,cfg in value.items())
    if key == '分发给总数公式':
        return f"每种物品总数＝主体人数×{value.get('每人', '未设定')}＋({value.get('加', '未设定')})，再受分发上限约束"
    if key == '分发削减顺序':
        return ' → '.join({'self': '自身份数', 'random_allies': '随机队友份数'}.get(v, str(v)) for v in value)
    if key == '制造分发':
        return '按己方存活主体人数分发，不计召唤物' if value == 'personal_split' else text_value(value)
    if key == '制造花费回合数':
        return quantity(value, '轮') + ('；常规制造进入待完成队列，不直接回复资源' if value == 0 else '')
    if key == 'damage_table':
        return '月相决定伤害与吸血分支' if value == 'moon_phase' else f'未解释的伤害表 {value}'
    if key == 'variant':
        return '火鼠裘；实际档位读取角色被动规则' if value == 'fire_rat_cloak' else f'未解释的受伤修正 {value}'
    return text_value(value)


def field_rows(data, labels, serial=''):
    return [row(labels.get(k, f'尚未核实的扩展字段 {k}'),
                formatted_field(k, v, serial) if k in labels else text_value(v), k)
            for k, v in data.items() if k not in ('effects', '_disabled')]


def _effect_detail(effect, spell, character, number):
    serial = character['serial']; kind = spell.get('blood_kind')
    rows = field_rows(effect, EFFECT_LABELS, serial)
    notes = []
    # Loader defaults are described, never presented as explicitly configured.
    if effect.get('type') == 1:
        for key in ('成功率', '可闪避性', '可反击性', '可贯穿性', '射程'):
            if key not in effect:
                rows.append(row(EFFECT_LABELS[key], DEFAULT))
        if not effect.get('成功率'):
            notes.append('伤害效果未配置正检定值时，敌对攻击取施法者最佳近战技能进行检定；不是零命中率。')
        notes.append('攻击失败时跳过本效果的伤害；成功后仍须处理允许的防御反应。困难、极难和大成功按战斗规则改变伤害取值；这里的伤害骰不是最终扣血保证值。')
    if '客体' not in effect:
        rows.insert(0, row('作用对象', DEFAULT))
    if effect.get('type') == 8:
        notes.append('领域的持续修正只在当前覆盖范围内读取；固定领域留在创建位置，跟随领域以来源的当前位置为中心。')
    if effect.get('type') == 3:
        notes.append('生命回复先抵消濒死溢出伤害，尚有余量才可脱离濒死；最终死亡者须用复活。生命、魔力回复受上限及回复修正影响。')
    if effect.get('type') == 4:
        notes.append('未允许叠加的同名同效果增益通常刷新剩余时间；同技能的不同效果分别保留。')
    if effect.get('type') == 5:
        notes.append('实际召唤数受同时存活、每场累计、可用位置与模板唯一性约束。纯召唤在名额为零时拒绝施放；同类召唤物按组共享先攻位置。声明持续回合时，各成员独立计时，到期退场并释放存活名额。')
    dynamic_keys = set()
    if kind in ('arcana', 'sacrifice', 'slash') or effect.get('consume_moon'):
        dynamic_keys.add('伤害骰')
    if kind == 'guard':
        dynamic_keys.update(('辅助效果', '辅助效果值'))
    if kind == 'store':
        dynamic_keys.add('回复hp')
    if kind == 'cape':
        dynamic_keys.add('回复mp')
    for item in rows:
        if item.get('source') in dynamic_keys:
            item['value'] = '由本技能动态结算替换（数据中的此项为基础声明，不作为最终效果）'
    if kind == 'arcana':
        notes.append('实际目标改为施法时锁定方向上的所有阵营单位，排除施法者；不使用原始单体目标或半径声明。目标用于确定方向。')
    if kind == 'war':
        notes.append('实际目标是动态半径内的所有阵营单位，但排除施法者自身；因此会波及队友，不会由这一冲击直接击中自己。')
    if kind in ('arcana', 'war'):
        for item in rows:
            if item.get('source') == '客体':
                item['value'] = '所有阵营（排除施法者，按血术实际几何过滤）'
            if item.get('source') == '作用半径':
                item['value'] = '按施放输入与专属规则决定，见资源/输入分节'
    return {'index': number, 'title': f"效果 {number} · {EFFECT_TYPES.get(effect.get('type'), '未解释效果')}",
            'target': target_text(effect.get('客体')), 'rows': rows, 'notes': notes,
            'unverified_fields': [k for k in effect if k not in EFFECT_LABELS and k != '_disabled']}


TEMPLATE_LABELS = {
 'HP': '生命', 'MP': '初始模板魔力', 'SAN': '理智', 'MOV': '行动力', 'STR': '力量', 'CON': '体质',
 'SIZ': '体型', 'DEX': '敏捷', 'APP': '外貌', 'INT': '智力', 'POW': '意志', 'EDU': '教育',
 '闪避': '闪避', '格挡': '格挡', '斗殴': '斗殴', '可反击': '可反击', '可反应': '可防御反应',
 '可格挡': '可格挡', '不可指定': '不可指定', '无主动行动': '无主动行动', '额外行动数': '额外行动数',
 'flying': '飞行', 'max_simultaneous': '同时存活上限', 'max_total_spawned': '每场累计召唤上限',
 'maintenance_mp': '每轮维持消耗月能', 'receives_round_mp_regen': '接受自然魔力回复',
 'replenish_allies': '以月能补满关联友军', 'unlimited_mp': '使用月能型独立魔力池',
 'keep_on_empty': '月能归零仍保留', 'clone_of_owner': '继承主人技能与部分属性',
 'protect_allies_on_hit': '受普通攻击时保护主人及同类克隆',
 'size_2x2': '占据2×2格', 'shield_block': '阻挡权重', 'shield_block_rate': '阻挡减伤比例',
 'react_block': '格挡反应权重', 'react_counter': '反击反应权重', 'react_dodge': '闪避反应权重',
 'ignore_unreactable_block': '可阻挡未开放常规反应的攻击', 'merge_group': '合体组', 'merge_result': '合体结果',
 '_is_merge_result': '合体结果标记', 'permanent_statuses': '常驻状态',
 'frost_summon': '给主人累积冰冻',
}
SKILL_TEMPLATE_LABELS = {'name': '招式', 'val': '技能值', 'dice': '伤害', 'hits': '段数',
 'on_whiff_aoe_dmg': '落空追加范围伤害', 'on_whiff_mp_cost': '落空追加消耗魔力',
 'mp_cost': '魔力消耗', 'cooldown_rounds': '冷却', 'skill_type': '招式类型',
 'zone_heal_hp': '领域回复生命', 'zone_radius': '领域半径', 'zone_duration': '领域持续'}


def template_details(spell, summons, items, character):
    result = []
    def visit(name, seen):
        if name in seen:
            return  # Also makes user-created cyclic meta/merge templates safe.
        seen = seen | {name}
        t = summons.get(name)
        if not isinstance(t, dict):
            result.append({'name': name, 'kind': '召唤物', 'rows': [], 'notes': ['模板不存在，无法确认产出属性。'], 'skills': []})
            return
        rows = []; notes = []; skills = []
        for k, v in t.items():
            if k in TEMPLATE_LABELS:
                value = ratio(v) if k == 'shield_block_rate' else text_value(v)
                rows.append(row(TEMPLATE_LABELS[k], value, k))
            elif k not in ('skills', 'spells', 'options', '_meta', 'unique_per_caster', 'frost_zone', 'frost_ball'):
                rows.append(row(f'尚未核实的模板字段 {k}', text_value(v), k))
        if t.get('_meta'):
            notes.append('召唤池：' + '、'.join(t.get('options', [])) + '。')
            if t.get('unique_per_caster'):
                notes.append('同一施法者按未获得过的模板抽取，已全部获得后不能再次抽取。')
        for raw in t.get('skills', []):
            if isinstance(raw, str):
                skills.append({'name': raw.split(':')[0], 'rows': [row('模板招式', raw)], 'effects': []})
            elif isinstance(raw, dict):
                skills.append({'name': raw.get('name', '未命名招式'),
                               'rows': [row(SKILL_TEMPLATE_LABELS.get(k, f'未核实字段 {k}'), text_value(v), k) for k,v in raw.items()], 'effects': []})
        for s in t.get('spells', []):
            skills.append({'name': s.get('name', '未命名法术'),
                           'rows': [row('时机', timing_text(s.get('timing', '2'))),
                                    row('MP费用', quantity(s['消耗mp'], 'MP') if '消耗mp' in s else DEFAULT)],
                           'effects': [_effect_detail(e, s, character, i+1) for i,e in enumerate(s.get('effects', []))]})
        if t.get('unlimited_mp'):
            notes.append('月能初值取本次实际支付的召唤魔力，而非模板的零魔力；该池不受模板MP值封顶。')
        if t.get('replenish_allies'):
            notes.append('轮首先处理自然回复，再检查低生命庇护，然后按先攻顺序以月能补满有关联的存活己方：先补生命、再补魔力，最后支付维持费。补满不是额外免费治疗。')
        if t.get('clone_of_owner'):
            notes.append('继承主人当前法术、剑、闪避、八维中的力量/敏捷/体质/体型/意志/幸运、魔力及魔力上限、移动与普攻伤害；继承项会覆盖对应模板基础值。')
        if t.get('protect_allies_on_hit'):
            notes.append('当前普通攻击入口在开始处理克隆被攻击时，给主人和其他具有此特性的克隆各授予一次伤害免疫；不能扩写为任意友方在所有伤害入口都受保护。')
        for key, label in [('frost_zone', '小雪带'), ('frost_ball', '雪球')]:
            if key in t:
                labels = {'dice':'伤害', 'mp_cost':'魔力消耗', 'range':'射程', 'success':'技能值',
                          'duration':'持续轮数', 'radius':'作用半径', 'stacks':'每轮冰冻层数'}
                skills.append({'name':label, 'rows':[row(labels.get(k,k), text_value(v), k) for k,v in t[key].items()], 'effects':[]})
        result.append({'name':name, 'kind':'召唤物', 'rows':rows, 'notes':notes, 'skills':skills})
        for candidate in t.get('options', []): visit(candidate, seen)
        if t.get('merge_result'): visit(t['merge_result'], seen)
    seen_names = set()
    for e in spell.get('effects', []):
        if e.get('召唤物模板'):
            visit(e['召唤物模板'], set())
        if e.get('制造物模板'):
            name = e['制造物模板']; t = items.get(name)
            if t is None:
                result.append({'name':name, 'kind':'制造物', 'rows':[], 'skills':[], 'notes':['模板不存在，无法确认食用效果。']})
            else:
                labels = {**EFFECT_LABELS, 'buff持续回合':'食用增益持续'}
                result.append({'name':name, 'kind':'制造物', 'rows':field_rows(t, labels), 'skills':[],
                               'notes':['制造生成物品，不等同于立即回复；食用时才按本模板结算。']})
    # Deduplicate expanded templates, preserving first appearance and order.
    return [t for t in result if not (t['name'] in seen_names or seen_names.add(t['name']))]


def spell_costs(s, c):
    kind = s.get('blood_kind'); costs = []
    for key, label, unit in [('消耗mp', '魔力', 'MP'), ('消耗san', '理智', 'SAN')]:
        costs.append(row(label, quantity(s[key], unit) if key in s else '未单独声明基础费用，依引擎费用规则', key if key in s else None))
    if s.get('_mp_formula'):
        formulas = {'summon_count_ceil_half': 'MP＝ceil(本次获准召唤数量÷2)，先截断名额，再计算费用',
                    'ceil_2d6_div_2': 'MP＝ceil(2d6掷骰结果÷2)'}
        costs[0]['value'] = formulas.get(s['_mp_formula'], f"未核实费用公式 {s['_mp_formula']}")
    if '_mp_formula_transformed' in s:
        costs.append(row('变身后的魔力费用', quantity(s['_mp_formula_transformed'], 'MP') + '，替换基础费用', '_mp_formula_transformed'))
    if kind == 'arcana':
        costs[0]['value'] = '实际MP＝max(1，floor(当前MP÷2))；有效投入＝max(实际MP，ceil(最大MP÷4))。伤害按有效投入计算，而非额外从MP扣除有效投入。'
        r = c.get('blood_rules', {})
        costs.append(row('血液与反噬', f"所需血量＝{r.get('arcana_base_blood', DEFAULT)} ml＋(有效投入−实际MP)×{r.get('arcana_substitute_ml', DEFAULT)} ml；优先用储血，缺口每{r.get('arcana_substitute_ml', DEFAULT)} ml向上折算1 HP反噬"))
    elif kind == 'guard':
        r = c.get('blood_rules', {})
        costs.append(row('血液/献血', f"默认{r.get('armor_ml', DEFAULT)} ml；blood参数可增加投入。未显式指定blood且储血不足一份时，改支付max(1，floor(当前HP÷2))生命；其他情况不自动献血。"))
    elif kind == 'blade':
        r = c.get('blood_rules', {})
        costs.append(row('塑造血武器', f"消耗{r.get('blade_ml', DEFAULT)} ml；不足时用全部储血，差额按ceil(缺血量÷{r.get('hp_to_ml', DEFAULT)}) HP补足，献血不得超过floor(当前HP÷2)。"))
    elif kind == 'store':
        r = c.get('blood_rules', {})
        costs.append(row('血球投入', f"units×{r.get('ball_ml', DEFAULT)} ml；回复units×{r.get('ball_heal', DEFAULT)} HP；不创建可保存的独立凝血球物品。"))
    elif kind == 'cape':
        r = c.get('blood_rules', {})
        costs.append(row('血换魔力', f"units×{r.get('cape_ml', DEFAULT)} ml → units×{r.get('cape_mp', DEFAULT)} MP；回复不得超过当前缺失魔力。"))
    elif kind == 'slash':
        r = c.get('blood_rules', {})
        costs.append(row('血液与段数', f"blood＝{r.get('slash_base_ml', DEFAULT)}＋n×{r.get('slash_extra_ml', DEFAULT)} ml；当前输入校验只允许n为0、1、2，对应1、2、3段（此段数限制写在引擎中）。"))
    elif kind == 'war':
        costs[0]['value'] = 'ceil(最大MP×20%)＋extra_mp；当前专用执行分支固定按20%计算，不采用通用费用路径的向下取整。'
        costs.append(row('生命', quantity(s.get('cost_hp_dice', DEFAULT), 'HP')))
    elif kind == 'spear':
        costs.append(row('血枪模式', f"create消耗{c.get('blood_rules', {}).get('spear_ml', DEFAULT)} ml；throw不再支付造枪血费，而是移除最早持有的一支枪。"))
    elif 'cost_blood_ml' in s:
        costs.append(row('声明血液费用', quantity(s['cost_blood_ml'], 'ml'), 'cost_blood_ml'))
    if 'cost_hp_dice' in s and kind != 'war': costs.append(row('生命', quantity(s['cost_hp_dice'], 'HP')))
    for key, resource in [('cost_hp_pct_current','当前生命'), ('cost_mp_pct_max','最大魔力'), ('cost_mp_pct_current','当前魔力')]:
        if key in s and kind != 'war': costs.append(row('比例费用', f"{resource}的{percent(s[key])}；通用执行路径向下取整、正费用至少1", key))
    if s.get('counter_required'):
        name, amount = s['counter_required']
        costs.append(row('资源门槛并消耗', f"需要{amount} {COUNTERS.get(name,name)}；当前引擎在成功提交施放时也会扣除此数量，并非只检查不扣除。", 'counter_required'))
    if s.get('cost_counter_type'):
        costs.append(row('计数器支付', f"{s.get('cost_counter_amount', DEFAULT)} {COUNTERS.get(s['cost_counter_type'], s['cost_counter_type'])}", 'cost_counter_type'))
    if any(e.get('type') == 13 for e in s.get('effects', [])):
        costs[0]['value'] = f"注册替身不支付；致命伤触发时才检查并支付{quantity(s.get('消耗mp', DEFAULT), 'MP')}"
    return costs


def spell_inputs(s, c):
    result = []
    kind = s.get('blood_kind')
    targets = list(dict.fromkeys(target_text(e.get('客体')) for e in s.get('effects', [])))
    result.append(row('目标选择', '；'.join(targets) or DEFAULT))
    result.append(row('目标解析', '所选单位只是候选，每个效果仍按自身作用对象解析；群体目标按物理单位去重。声明半径时以施法者为中心过滤，不以所选敌人为圆心。'))
    if s.get('weapon_required'): result.append(row('武器要求', WEAPONS.get(s['weapon_required'], f"未核实武器 {s['weapon_required']}")))
    if s.get('phase') not in (None, 0):
        result.append(row('阶段要求', '非战斗资料项，当前战斗不可用' if s['phase'] == 99 else PHASES.get(c['serial'], {}).get(s['phase'], f"阶段{s['phase']}")))
    params = {
      'arcana': [('目标', '选择射程内敌人以锁定施法方向，真正伤害对象为该方向内所有阵营单位'), ('mode=cancel', '取消本技能正在进行的吟唱，已支付费用不返还；再次无参数使用同技为续唱')],
      'sacrifice': [('目标', '起手选择敌方单体；释放时再次检查原目标存活与距离'), ('mode=cancel', '取消正在进行的同名吟唱，不返还费用；再次无参数使用同技为续唱')],
      'guard': [('blood=毫升', '可选，必须为每份护甲血量的正整数倍，并受护甲上限约束；不接受units')],
      'blade': [('form=sword / bow', '可选，默认sword；血刃支持月斩，血弓用于弓术远程普攻')],
      'store': [('units=份数', f"可选，默认1，范围1至{c.get('blood_rules', {}).get('heal_max_units', DEFAULT)}；不得超量投入"), ('target=用户ID', '可选，须为范围内同队可治疗单位；不指定时优先选择缺血比例较高的合格目标')],
      'cape': [('units=份数', '可选，默认1，正整数；按实际缺蓝和储血校验')],
      'slash': [('blood=毫升', '可选，以基础血量为起点、按每段追加血量递增；见费用表'), ('moon=序号', f"可选，1至{len(c.get('blood_rules', {}).get('moon_phases', []))}；未指定时逐段随机抽月相")],
      'war': [('extra_mp=追加魔力', '可选，默认0，非负整数；支付额外魔力扩大冲击半径')],
      'spear': [('mode=create / throw', '可选，默认create；一次凝聚一支或投掷最早持有的一支；throw还须选择敌方目标')],
    }
    if kind:
        result.extend(row(k,v) for k,v in params.get(kind, []))
        result.append(row('参数限制', '只接受本技能列出的参数，不接受重复项；数值参数须为非负整数。未知参数在支付前拒绝。'))
    elif any(e.get('type') == 19 for e in s.get('effects', [])):
        result.append(row('复活选择', '从当前符合条件的墓地候选选择；不选择时使用引擎候选选择规则。'))
    return result


def rule_notes(s, c):
    """Verified exceptions, with code paths recorded in AUDIT_PATHS below."""
    notes = []; serial = c['serial']; name = s['name']; kind = s.get('blood_kind')
    if any(e.get('type') == 1 for e in s.get('effects', [])):
        notes.append('不能闪避或反击不等于自动命中。普通攻击效果按效果索引依次结算；前段击倒目标后，后续对该倒下目标的伤害条目会跳过，不自动转火。')
    if any(e.get('吸血比例') for e in s.get('effects', [])):
        notes.append('当前通用法术吸血在有效伤害大于零时，以护盾吸收前的修正伤害乘吸血比例向下取整；月斩另以目标实际可失去生命为基数。回复受施法者生命上限约束，不能统一写成“按最终扣血吸取”。')
    if s.get('default_persist') is not None:
        notes.append('普通增益持续回合耗尽后失效；默认延续增益到期后保留生效，效果自身的默认延续性设置优先于技能设置。来源绑定、阶段清理及专用机制另行处理。')
    if str(s.get('timing', '2')).find('1') >= 0:
        notes.append('开战时自动尝试施放被动，隔离状态会阻止该次自动施放；有费用的被动仍经费用检查。未声明自动刷新的限时被动不会因到期而自动重施。')
    if s.get('吟唱回合') and not kind:
        notes.append('普通吟唱起手即支付费用，先保存效果与原目标，随后由轮次推进释放；与需要反复主动续唱的血术流程不同。')
    if serial == 'Y9' and name == '生之矛':
        notes.append('当前标准敌方攻击分支会造成伤害与吸血，但没有挂接数据所声明的敌方延迟回复。友方分支登记主回复后还执行敌方回复公式块，因此同一友方会出现第二条较小分轮回复；这与字段名称的直观含义不同。')
    if serial == 'Y9' and name == '阶段转换':
        notes.append('被动注册和阈值触发分别经过理智处理：技能顶层理智费用用于注册；阈值成功时另读取效果触发理智费用，缺省按引擎触发费用处理。转换清除自己的旧领域，并点燃已有生灵。')
    if serial == 'Y14':
        if any(e.get('target_phase') for e in s.get('effects', [])):
            notes.append('切换保留独立效果和极限槽，清除形态绑定的替身/模式/冲刺效果，并清空金环；进入最终幻想默认迅捷。已永久弹出的阶段不可加载。')
        if name == '光速救援':
            notes.append('实现边界：当前传送直接把地图占位写到所选友方坐标，没有搜索邻近空格；护盾目标在传送前已解析。数据的友方群体标记和射程声明没有在此传送入口逐项执行校验，不可解释为完整的“安全落点救援”。')
        if name == '残机替身':
            notes.append('先检查仍在魂斗罗且替身未消耗、魔力足够，再保留重生生命、搜索重生位置并永久弹出魂斗罗。注册免费；触发费用、重生数值见费用及效果表。')
        if name == '极限槽':
            notes.append('每个真实单位每轮分别记录伤害输出、实际受伤与勇气成功反击，反击成功即是事件，不要求反击造成实际生命损失；己方其他主体第一次濒死另有奖励。')
        if name == '金环':
            notes.append('闪避与冲刺达到距离共用该轮的金环获取记录；切换卡带会清空金环。')
    if serial == 'Y15' and name == '葬火流光':
        notes.append('按当前效果顺序先创建领域，再执行群体直接伤害；领域每轮伤害不等于施放时多打一遍。')
    if serial == 'Y16':
        if name == '幻造兵武·即兴短句':
            notes.append('弓形态每段在基础骰上加弓术伤害骰；只进行一次共享写作判定，写作收益同时影响每段，充能只执行一次。法杖额外充能不适用于弓/剑。')
        if name == '辉月女神的祝福':
            notes.append('低生命门槛为严格小于，不含等于；轮首先检查追加庇护再月能补满。已有同类免伤时不重复购买。')
        if name == '月光王座全额率输出':
            notes.append('起手检查月亮与月能门槛，提交时消耗全部月能并销毁月亮；吟唱释放使用已保存的伤害，不再重复支付月能。')
    if kind:
        notes.append('血术先验证输入、目标、状态与资源，再提交支付；昏迷、濒死或禁止主动施法状态不可操纵血液。吟唱中允许续唱和部分附加血术，不能任意发动其他主法术。')
    if kind == 'arcana':
        notes.extend(['当前几何实作为沿锁定方向向前10格、横向区间[−5,5)的矩形；不按原设定擅自解释成锥形或全屏。来源与方向在起手锁定。此几何尺寸为引擎常量。',
                      '支付HP反噬时方向随机化；若反噬导致倒下则立即以起手原点的10×10区域爆发；若起手支付后MP归零则立即昏迷并中止吟唱，不会正常释放。',
                      '命中造成实际生命损失后，按有效投入×50 ml尝试收血；召唤物不产血，同一主体累计抽取受血源上限约束。50 ml系数当前位于引擎专用结算。'])
    if kind in ('arcana', 'sacrifice'):
        notes.append('起手算一次主动作，剩余进度须再次使用同名技能续唱；取消不退已付费用，续唱不重复支付起手资源。')
    if kind == 'sacrifice':
        notes.extend(['释放前原目标已倒下或离开射程则消散，施法者不为该次失败主动进入濒死；成功释放前记录死亡追击目标，然后将自身置入濒死。',
                      '只有实际生命损失会中断血术吟唱，纯护盾吸收不会；中断时按当前生命和当前魔力的声明比例向上取整扣除，并标记重伤。',
                      '施法者随后实际死亡时，若记录目标仍存活，追加其最大生命的十分之一伤害（向下取整，至少1）；自身被有效治疗恢复正生命会清除追击记录。'])
    if kind == 'store':
        notes.append('当前“轻伤”要求目标生命至少为上限一半、低于上限且大于零，无濒死/昏迷/重伤；若前units−1份已足以补满，则拒绝额外一份。')
    if kind == 'war':
        notes.append('每波结束若施法者仍存活、储血达到连锁量且仍有目标，就扣血再次冲击；连锁可以使用战前已有储血，不必只靠本波击杀积累。连锁不重复支付MP/HP/SAN。召唤物死亡不给主体击杀收血奖励，同一主体不重复给奖励。')
    if kind == 'slash':
        notes.append('收血上限在本次施放的各段间共享，并非每段重置；月相额外吸取在基础伤害后另行处理，目标已倒下时不会继续抽取。')
    if kind == 'spear':
        notes.append('生成血枪使用持有物与耐久列表，不创建血液长枪模板所描述的独立召唤物。投掷在伤害检定前移除枪，即使落空也消耗。普攻/反击磨损及成功格挡额外磨损按武器使用入口执行。')
    if s.get('phase') == 99:
        notes.append('这是保留的非战斗资料。下列表格只说明数据声明；当前战斗入口不提供这项能力，不能把声明效果当成已实现的战斗选项。')
    return notes


def settlement_steps(s, c):
    kind = s.get('blood_kind'); steps = []
    if s.get('phase') == 99:
        return ['当前战斗不可发动；仅供查阅原有数据声明。']
    if any(e.get('type') == 13 for e in s.get('effects', [])):
        return ['入场或加载该形态时免费注册替身。', '致命伤触发时检查形态、使用记录及所需魔力。', '满足条件才支付触发费用并执行重生、弹出卡带；不满足则继续正常濒死/死亡处理。']
    if kind:
        steps = ['先解析本技能允许的参数，校验当前状态、目标/距离及全部资源；拒绝时不支付起手费用。',
                 '确认后按计划扣除储血、魔力、理智与生命代价。']
        if kind in ('arcana', 'sacrifice'):
            steps.append('起手保存目标/方向与动态伤害，登记剩余吟唱；之后同名无参数使用推进进度，mode=cancel取消。')
            steps.append('完成时移除吟唱状态，按专用释放分支检查目标并依序结算；反噬、中断、耗空魔力分支见特殊规则。')
        elif kind in ('guard', 'blade'):
            steps.append('以本次投入生成护甲或血武器状态；同形态/同类仍有效时按专用限制拒绝重复创建。')
        elif kind == 'spear':
            steps.append('create登记一支有耐久的持枪；throw先移除一支持枪，再按弓术检定投掷伤害。')
        else:
            steps.append('按实际投入改写占位伤害、回复量或目标范围，再逐效果逐目标结算。')
            steps.append('结算命中后的收血、击杀奖励、回复或连锁；具体条件与上限见特殊规则。')
    else:
        steps = ['检查技能时机、阶段/武器、冷却、可用目标、召唤或领域名额以及次数限制。',
                 '按当前骰式/公式和费用修正支付资源；正魔力费用经减免后仍至少支付1 MP，零费用不凭空变成正费用。']
        if s.get('吟唱回合'):
            steps.append('支付后登记吟唱，当前不立即执行效果；由轮次推进释放保存的效果。')
        else:
            for i, e in enumerate(s.get('effects', []), 1):
                action = {1:'先作攻击检定；成功才处理防御反应与伤害，失败跳过本效果',
                          2:'掷取护盾容量并授予各合法目标', 3:'结算所列资源回复',
                          4:'授予或刷新对应修正', 5:'按实际获准数量创建召唤单位',
                          6:'建立制造/分发结果', 8:'创建领域，按设置处理立即脉冲与后续轮首脉冲',
                          9:'登记生命阈值监视，不立即转换', 11:'登记战意来源并在轮次流程更新',
                          12:'登记受伤时的修正机制', 14:'创建或更新专属计数器',
                          17:'执行阶段/武器切换并重读可用技能', 18:'按传送入口改写所选友方位置的地图占位',
                          19:'从合格墓地候选中恢复单位'}.get(e.get('type'), '执行已声明效果；未核实类型不推断额外行为')
                steps.append(f"效果{i}：{action}；作用对象为{target_text(e.get('客体'))}。")
        steps.append('提交的施放才记录每场次数与相关冷却；后续持续效果、来源死亡和阶段切换依各自规则清理。')
    return steps


def special_numbers(s, c):
    rows = []; r = c.get('blood_rules', {}); kind = s.get('blood_kind')
    if kind == 'arcana':
        e = s.get('effects', [{}])[0]
        rows += [row('实际基础伤害', f"有效魔力投入×{e.get('damage_mp_multiplier', DEFAULT)}"),
                 row('攻击额外奖励骰', r.get('arcana_bonus_dice', DEFAULT)), row('同一血源累计上限', f"{r.get('source_cap', DEFAULT)} ml")]
    if kind == 'guard':
        rows += [row('血换护甲', f"每{r.get('armor_ml',DEFAULT)} ml获得{r.get('armor_per_unit',DEFAULT)}点固定减伤，上限{r.get('armor_cap',DEFAULT)}"),
                 row('护甲持续', f"{r.get('duration', DEFAULT)} 轮（实际生成状态读取血术持续参数）")]
    if kind == 'blade':
        rows += [row('血刃伤害', r.get('blade_damage', DEFAULT)), row('血弓伤害', r.get('bow_damage', DEFAULT)),
                 row('血弓射程', f"{r.get('bow_range', DEFAULT)} 格"), row('状态持续', f"{r.get('duration', DEFAULT)} 轮")]
    if kind == 'war':
        rows += [row('实际半径', f"{r.get('war_radius',DEFAULT)}＋extra_mp×{r.get('war_extra_radius',DEFAULT)} 格"),
                 row('连锁血量', f"{r.get('chain_ml',DEFAULT)} ml"), row('每具主体尸体收血', f"最多{r.get('corpse_ml',DEFAULT)} ml，仍受血源累计上限{r.get('source_cap',DEFAULT)} ml约束")]
    if kind == 'slash':
        rows += [row('月相'+str(i), f'{name}：{dice}' + ('；带吸取分支' if drain else '')) for i,(name,dice,drain) in enumerate(r.get('moon_phases', []),1)]
        rows += [row('本次施放共享收血上限', f"{r.get('slash_harvest_cap', DEFAULT)} ml"),
                 row('命中收血系数', f"每实际损失1 HP尝试收血{r.get('slash_harvest_ml_per_hp',DEFAULT)} ml")]
    if kind == 'spear':
        rows += [row('持有上限', r.get('spear_cap', DEFAULT)), row('每支耐久', r.get('spear_uses',DEFAULT)),
                 row('近战伤害', r.get('spear_melee',DEFAULT)), row('投掷伤害', r.get('spear_throw',DEFAULT)),
                 row('投掷射程', f"{r.get('spear_range',DEFAULT)} 格"), row('投掷检定', f"弓术 {c.get('attrs',{}).get('弓术', DEFAULT)}")]
    if any(e.get('consume_moon') for e in s.get('effects', [])):
        rows.append(row('月能起手门槛', f"严格大于{c.get('passive_rules',{}).get('moon',{}).get('ultimate_threshold', DEFAULT)}，须有存活月亮"))
    return rows


def limitation_rows(s):
    result = []
    if s.get('phase') == 99:
        result.append(row('战斗可用性', '非战斗技能，当前战斗不可用'))
    if '_once_per_battle' in s: result.append(row('每场次数', '限一次' if s['_once_per_battle'] else '未启用每场一次限制'))
    if 'cooldown_rounds' in s: result.append(row('技能冷却', quantity(s['cooldown_rounds'], '轮')))
    if '吟唱回合' in s: result.append(row('吟唱', f"{s['吟唱回合']}" + ('次主动作（含起手）' if s.get('blood_kind') else '轮，起手支付后等待轮次推进')))
    if 'default_persist' in s: result.append(row('默认延续性', '启用' if s['default_persist'] else '未启用'))
    for key, label in [('chant_interruptible','可中断'), ('chant_interrupt_hp_pct','中断当前HP损失比例'), ('chant_interrupt_mp_pct','中断当前MP损失比例')]:
        if key in s: result.append(row(label, percent(s[key])+'（向上取整，至少1）' if key.endswith('_pct') else text_value(s[key])))
    return result


# This is a content audit index, not runtime engine introspection. No combat
# engine is imported or executed to format a catalog or an article.
AUDIT_PATHS = {
 'common': ['battle_engine.py:_execute_spell', 'battle_engine.py:_execute_spell_plan', 'battle_engine.py:_resolve_effect_targets'],
 'blood': ['battle_engine.py:_y17_plan', 'battle_engine.py:_y17_execute', 'battle_engine.py:_y17_continue_chant', 'battle_engine.py:_y17_collect_hit'],
 'summon': ['battle_engine.py:_execute_summon_effect', 'battle_engine.py:_create_summon'],
 'zone': ['battle_engine.py:_execute_misc_effect', 'battle_engine.py:advance_round'],
}


def _section(key, title, paragraphs=None, rows=None, effects=None, templates=None, ordered=False):
    return {'id':key, 'title':title, 'paragraphs':paragraphs or [], 'rows':rows or [],
            'effects':effects or [], 'templates':templates or [], 'ordered':ordered}


def _y1_y2_content(s, c, summons):
    """Player-facing copy for the five reviewed skills; tuning stays in data."""
    identity = (c['serial'], s['name'], s.get('phase', 0))
    if identity not in {
        ('Y1', '斗殴精通', 0), ('Y1', '火焰附魔', 0),
        ('Y2', '火球术', 0), ('Y2', '召唤使魔', 0), ('Y2', '速攻', 0),
    }:
        return None

    costs = [f'{text_value(s[k])} {unit}' for k, unit in
             (('消耗mp', 'MP'), ('消耗san', 'SAN')) if k in s]
    requirements = [row('时机', timing_text(s.get('timing', '2')))]
    if costs:
        requirements.append(row('费用', ' / '.join(costs)))
    effects = s.get('effects', [])
    effect = effects[0] if effects else {}
    paragraphs = []
    summon_rows = []
    limits = ''
    duration = ('持续生效' if s.get('default_persist') else
                f"持续 {text_value(effect.get('持续回合', DEFAULT))} 回合")

    def reactions(e):
        choices = [label for key, label in
                   (('可闪避性', '闪避'), ('可反击性', '反击'), ('可格挡性', '格挡'))
                   if e.get(key, 1)]
        return '或'.join(choices) if len(choices) < 3 else '、'.join(choices[:-1]) + '或' + choices[-1]

    def attack_value(e):
        return str(e['成功率']) if e.get('成功率', 0) > 0 else '自身最佳近战技能'

    if identity == ('Y1', '斗殴精通', 0):
        token = str(effect.get('辅助效果值', '')).strip()
        bonus = re.fullmatch(r'b(\d*)|(\d+)b', token)
        reward = (f"{bonus.group(1) or bonus.group(2) or '1'} 颗攻击奖励骰" if bonus else
                  formatted_field('辅助效果值', effect.get('辅助效果值', DEFAULT)))
        description = ARTICLES[identity].format(reward=reward, duration=duration)
        costs = [target_text(effect.get('客体'))]
    elif identity == ('Y2', '速攻', 0):
        gain = re.sub(r'(?=[+−-])', ' ', str(effect.get('技能加减值', DEFAULT)), count=1)
        description = ARTICLES[identity].format(gain=gain, duration=duration)
        limits = target_text(effect.get('客体'))
    elif identity == ('Y1', '火焰附魔', 0):
        description = ARTICLES[identity].format(skill=attack_value(effect),
                                               damage=text_value(effect.get('伤害骰', DEFAULT)),
                                               reactions=reactions(effect))
        limits = target_text(effect.get('客体'))
        if '射程' in effect:
            requirements.append(row('射程', f"{text_value(effect['射程'])} 格"))
            limits += f"｜射程 {text_value(effect['射程'])} 格"
        paragraphs = [
            f"命中后附加灼烧，每轮造成 {text_value(effect.get('每回合伤害骰', DEFAULT))} 伤害，"
            f"持续 {text_value(effect.get('持续回合', DEFAULT))} 轮。多次命中产生的灼烧分别生效。",
        ]
    elif identity == ('Y2', '火球术', 0):
        attacks = [e for e in effects if e.get('type') == 1]
        count = len(attacks)
        count_text = '零一二三四五六七八九十'[count] if count <= 10 else str(count)
        values = list(dict.fromkeys(attack_value(e) for e in attacks))
        damage = '、'.join(text_value(e.get('伤害骰', DEFAULT)) for e in attacks)
        description = ARTICLES[identity].format(count=count_text, values='、'.join(values), damage=damage)
        limits = target_text(effect.get('客体'))
        ranges = list(dict.fromkeys(text_value(e.get('射程', DEFAULT)) for e in attacks))
        limits += f"｜射程 {'、'.join(ranges)} 格"
        paragraphs = [f'目标仅可{reactions(effect)}。某枚火球未命中时，继续结算其余火球；目标倒下后停止后续攻击。']
    else:
        template = summons.get(effect.get('召唤物模板'), {})
        count = text_value(effect.get('召唤个数', DEFAULT))
        cap = (f"，同一施法者最多同时拥有 {template['max_simultaneous']} 只"
               if template.get('max_simultaneous') is not None else '')
        description = ARTICLES[identity].format(count=count, cap=cap)
        paragraphs = [
            f'同一施法者的使魔组成召唤组，共享先攻位置，各自保留生命与状态。使魔持续 {text_value(effect.get("持续回合", DEFAULT))} 回合，到期退场；主人死亡时随之退场。',
        ]
        for key, label in [('HP', '生命'), ('MOV', '基础行动力'), ('DEX', '敏捷')]:
            if key in template:
                summon_rows.append(row(label, text_value(template[key]), key))
        for skill in template.get('skills', []):
            if isinstance(skill, str):
                parts = skill.split()
                skill_name, _, value = parts[0].partition(':')
                summon_rows.append(row(skill_name, value))
                if len(parts) > 1:
                    summon_rows.append(row('攻击伤害', parts[1]))
        for key, label in [('闪避', '闪避'), ('flying', '飞行')]:
            if key in template:
                summon_rows.append(row(label, text_value(template[key]), key))
        defense = [label for key, label in [('可反应', '闪避'), ('可反击', '反击')]
                   if template.get(key)]
        if defense:
            summon_rows.append(row('防御反应', '、'.join(defense)))

    requirements.append(row('目标', target_text(effect.get('客体'))))
    sections = [_section('inputs', '施放要求', rows=requirements)]
    if summon_rows:
        sections.append(_section('templates', '使魔属性', rows=summon_rows))
    if paragraphs:
        sections.append(_section('effects', '技能效果', paragraphs))
    facts = [f"{r['label']}：{r['value']}" for r in summon_rows] + paragraphs
    return dict(description=description, cost=' / '.join(costs), limits=limits,
                timing=timing_text(s.get('timing', '2')).replace(' / ', '／'), facts=facts,
                versions=[dict(id=v['id'], label=v['label'], overview=description,
                               sections=deepcopy(sections)) for v in VERSIONS])


def _y3_y4_content(s, c):
    """Reviewed Y3/Y4 copy, using the same sections as Y1/Y2."""
    identity = (c['serial'], s['name'], s.get('phase', 0))
    if identity not in {('Y3', '斗殴强化', 0), ('Y3', '附魔术', 0),
                        ('Y4', '星闪攻击', 0), ('Y4', '演唱', 0)}:
        return None
    effects = s.get('effects', [])
    effect = effects[0] if effects else {}
    costs = [f'{text_value(s[k])} {unit}' for k, unit in
             (('消耗mp', 'MP'), ('消耗san', 'SAN')) if k in s]
    requirements = [row('时机', timing_text(s.get('timing', '2')))]
    if costs:
        requirements.append(row('费用', ' / '.join(costs)))

    def dice_text(token):
        token = str(token).strip()
        match = re.fullmatch(r'([bp])(\d*)|(\d+)([bp])', token)
        if match:
            kind = match.group(1) or match.group(4)
            count = match.group(2) or match.group(3) or '1'
            return f'{count} 颗攻击' + ('奖励骰' if kind == 'b' else '惩罚骰')
        if isinstance(token, str) and re.fullmatch(r'-?\d+', token):
            count = int(token)
            return f'{abs(count)} 颗攻击' + ('奖励骰' if count > 0 else '惩罚骰')
        return token or DEFAULT

    facts = []
    limits = target_text(effect.get('客体'))
    duration = ('持续生效' if s.get('default_persist') else
                f"持续 {text_value(effect.get('持续回合', DEFAULT))} 轮")
    if identity == ('Y3', '斗殴强化', 0):
        description = ARTICLES[identity].format(
            reward=dice_text(effect.get('辅助效果值', '')), duration=duration)
    elif identity == ('Y3', '附魔术', 0):
        description = ARTICLES[identity].format(
            damage=text_value(effect.get('辅助效果值', DEFAULT)), duration=duration)
    elif identity == ('Y4', '星闪攻击', 0):
        attacks = [e for e in effects if e.get('type') == 1]
        count = len(attacks)
        count_text = '两' if count == 2 else '零一二三四五六七八九十'[count] if count <= 10 else str(count)
        values = '、'.join(dict.fromkeys(str(e['成功率']) if e.get('成功率', 0) > 0
                                     else '自身最佳近战技能' for e in attacks))
        reward = '、'.join(dict.fromkeys(dice_text(e.get('成功率奖惩骰', 0)) for e in attacks))
        description = ARTICLES[identity].format(count=count_text, values=values, reward=reward,
            damage='、'.join(text_value(e.get('伤害骰', DEFAULT)) for e in attacks))
        ranges = '、'.join(dict.fromkeys(text_value(e.get('射程', DEFAULT)) for e in attacks))
        requirements.append(row('射程', f'{ranges} 格'))
        limits += f'｜射程 {ranges} 格'
        defenses = '；'.join(dict.fromkeys('、'.join(label for key, label in
            (('可闪避性', '闪避'), ('可反击性', '反击'), ('可格挡性', '格挡'))
            if e.get(key, 1)) for e in attacks))
        facts = [f'目标仅可{defenses}。每段攻击分别处理格挡；某段攻击未命中或被格挡时，继续结算其余攻击；目标倒下后停止后续攻击。',
                 '贯穿：' + '、'.join(dict.fromkeys('是' if e.get('可贯穿性', 1) else '否' for e in attacks)) + '。']
    else:
        rounds = text_value(effect.get('持续回合', DEFAULT))
        penalty = dice_text(effect.get('辅助效果值', ''))
        description = ARTICLES[identity].format(penalty=penalty, duration=rounds)
        facts = [f'施放后直接对全场存活敌人生效，包含召唤物。重复施放时，各目标已有的演唱效果刷新至至少剩余 {rounds} 轮，同名效果保持 {penalty}。持续时间结束后解除。']
    requirements.append(row('目标', target_text(effect.get('客体'))))
    sections = [_section('inputs', '施放要求', rows=requirements)]
    if facts:
        sections.append(_section('effects', '技能效果', facts))
    return dict(description=description, cost=' / '.join(costs), limits=limits,
                timing=timing_text(s.get('timing', '2')), facts=facts,
                versions=[dict(id=v['id'], label=v['label'], overview=description,
                               sections=deepcopy(sections)) for v in VERSIONS])


def _y7_spirit_paragraphs(e):
    """Shared skill/passive prose; thresholds and probabilities remain card-owned."""
    def value(key):
        return text_value(e.get(key, DEFAULT))

    cap = e.get('战意惩罚骰上限')
    p1 = min(1, cap) if isinstance(cap, (int, float)) else DEFAULT
    p2 = min(2, cap) if isinstance(cap, (int, float)) else DEFAULT
    rate = value('战意削弱概率')
    if e.get('战意削弱概率奖励层') != e.get('战意削弱概率'):
        rate += (f"%（战意至多为 {value('战意奖励骰阈值')} 层时为 "
                 f"{value('战意削弱概率奖励层')}%）")
    else:
        rate += '%'
    return [
        f"敌方主体分别记录战意，初始为 {value('战意主体初始层数')} 层。首次轮首记录初始层数，此后每轮有 {value('战意主体削弱概率')}% 概率减少 1 层，最低为 0 层。"
        f"战意至多为 {value('战意惩罚1阈值')} 层时，攻击检定承受 {p1} 颗惩罚骰；至多为 {value('战意惩罚2阈值')} 层时，改为 {p2} 颗惩罚骰；"
        f"至多为 {value('战意主体减伤阈值')} 层时，造成伤害降低 {value('战意主体减伤百分比')}%；降至 {value('战意主体沉默阈值')} 层时，另受 4 级沉默，继续留在战场。",
        f"敌方召唤物按召唤组共享战意，初始为 {value('战意初始层数')} 层。首次轮首记录初始层数，此后每轮有 {rate} 概率减少 1 层。"
        f"战意至多为 {value('战意惩罚1阈值')} 层时，攻击检定承受 {p1} 颗惩罚骰；至多为 {value('战意惩罚2阈值')} 层时，改为 {p2} 颗惩罚骰；"
        '降至 0 层时，整组撤离。更换组内代表保留已有战意。',
        '环花暖生命大于 0、留在战场且处于隔离状态之外时，推进战意削弱并施加主体压制。条件中断时，已有战意层数保留，召唤组已获得的攻击惩罚保留。多个来源共同作用时，每个主体或召唤组每轮处理一次，主体同类压制取较强效果。',
    ]


def _y7_y8_content(s, c, items):
    """Approved Y7/Y8 copy in the same overview/inputs/effects layout as Y1/Y2."""
    identity = (c['serial'], s['name'], s.get('phase', 0))
    if identity not in {
        ('Y7', '战意值削弱', 0), ('Y7', '治愈领域', 0), ('Y7', '返生之花', 0),
        ('Y8', '美味的蛋糕', 0), ('Y8', '甜甜的蛋糕', 0), ('Y8', '品尝蛋糕', 0),
        ('Y8', '最后的款待-恢复', 0), ('Y8', '轻盈蛋糕', 0), ('Y8', '幸运蛋糕', 0),
        ('Y8', '最后的款待-进攻', 0),
    }:
        return None

    effects = s.get('effects', [])
    e = effects[0] if effects else {}
    def value(key):
        return text_value(e.get(key, DEFAULT))

    def count_word(value):
        return {1: '一', 2: '两'}.get(value, str(value))

    costs = [f'{text_value(s[k])} {unit}' for k, unit in
             (('消耗mp', 'MP'), ('消耗san', 'SAN')) if k in s]
    requirements = [row('时机', timing_text(s.get('timing', '2')))]
    if costs:
        requirements.append(row('费用', ' / '.join(costs)))
    name = s['name']
    paragraphs = []
    template = ARTICLES[identity]
    cake_use = [
        '蛋糕可供自身或同队存活、濒死单位食用。使用时优先消耗使用者个人库存中的首个可用蛋糕，再取用己方共享制造物池中的首个成品。',
        '同种蛋糕的增益重复食用时，剩余持续时间取原值与该蛋糕完整持续时间中的较大值。同种增益保留一份。',
        '携带个人生命蛋糕的单位进入濒死时，自动食用生命蛋糕，直至脱离濒死或可用生命蛋糕耗尽。',
    ]
    item_names = {'蛋糕HP': '生命蛋糕', '蛋糕MP': '魔力蛋糕',
                  '蛋糕轻盈': '轻盈蛋糕', '蛋糕幸运': '幸运蛋糕'}

    def item_benefit(effect):
        item = items.get(effect.get('制造物模板'), {})
        duration = text_value(item.get('buff持续回合', DEFAULT))
        healing = [f"{text_value(item[key])} {unit}" for key, unit in
                   (('回复hp', 'HP'), ('回复mp', 'MP'), ('回复san', 'SAN')) if key in item]
        gain = re.sub(r'(?=[+−-])', ' ', str(item.get('技能加减值', DEFAULT)), count=1)
        gain = gain.replace('MOV', '行动力')
        if healing:
            result = '食用后回复 ' + ' 与 '.join(healing)
            if effect.get('消费追加回复hp'):
                result += f"，另触发一次 {text_value(effect['消费追加回复hp'])} HP 的品尝回复"
            if '技能加减值' in item:
                result += f'，并获得{gain}，持续 {duration} 回合'
            return result + '。'
        if item.get('辅助效果') == '全D100检定奖励骰':
            token = str(item.get('辅助效果值', ''))
            match = re.fullmatch(r'b(\d*)|(\d+)b', token)
            dice = (match.group(1) or match.group(2) or '1') if match else DEFAULT
            return f'食用后，所有 D100 检定获得 {dice} 颗奖励骰，持续 {duration} 回合。'
        return f'食用后获得{gain}，持续 {duration} 回合。'

    def feast_paragraphs(effect):
        overrides = effect.get('分发人数覆盖', {})
        one = overrides.get('1', overrides.get(1, {}))
        two = overrides.get('2', overrides.get(2, {}))
        keep = effect.get('分发自留', DEFAULT)
        ally = effect.get('分发队友每人', DEFAULT)
        formula = effect.get('分发给总数公式', {})
        offset = formula.get('加', DEFAULT)
        signed_offset = (f'−{abs(offset)}' if offset < 0 else f'+{offset}') if isinstance(offset, (int, float)) else str(offset)
        return [
            '按己方存活或濒死主体人数分配，人数包含比哈米。两类蛋糕分别分配至个人库存。',
            f"仅有 1 人时，比哈米每类获得 {one.get('分发自留', DEFAULT)} 个；共 2 人时，比哈米每类获得 {two.get('分发自留', keep)} 个，"
            f"队友每类获得 {two.get('分发队友每人', ally)} 个；共 3 人时，比哈米每类获得 {keep} 个，两名队友每类各获得 {ally} 个。",
            f"4 人及以上时，每类总量为 min({effect.get('分发给总数上限', DEFAULT)}，{formula.get('每人', DEFAULT)}×人数{signed_offset})。"
            f'先按比哈米 {keep} 个、每名队友 {ally} 个分配；超额部分先削减比哈米份额，再逐个随机削减尚有份额的队友。两类蛋糕分别随机削减。',
        ]

    if c['serial'] == 'Y7':
        if name == '战意值削弱':
            description = template
            target = '敌方主体与召唤物'
            paragraphs = _y7_spirit_paragraphs(e)
        elif name == '治愈领域':
            description = template.format(radius=value('作用半径'), duration=value('持续回合'),
                                          hp=value('回复hp'), mp=value('回复mp'))
            target = '自身及范围内己方单位'
            paragraphs = [
                f"施放时立即回复{count_word(e.get('领域立即结算', DEFAULT))}次，随后于轮首回复 {value('领域轮首结算次数')} 次。各目标、各项回复分别掷骰。轮首先为范围内的濒死单位急救，同一单位在该次轮首获得的同类回复计入本轮回复。",
                '隔离中的单位及已点燃的召唤物排除在回复对象之外。',
                f"每名施法者同时至多维持 {value('领域同时上限')} 个治愈领域。已有领域时，重复施放被拒绝，保留本次费用。",
            ]
        else:
            uses = '每场战斗限用 1 次' if s.get('_once_per_battle') else ''
            description = template.format(hp_ratio=ratio(e.get('复活回复比例', DEFAULT)), uses=uses,
                                          cooldown=text_value(s.get('cooldown_rounds', DEFAULT)))
            target = '符合死亡轮窗口的己方阵亡单位'
            paragraphs = [
                f"目标须有阵亡记录，且施放轮次与阵亡轮次之差至多为 {value('死亡轮窗口')}；阵亡当轮可以复活。每条阵亡记录可复活一次。",
                '可以指定符合条件的阵亡单位。省略选择时，优先复活候选召唤物的主人；其余情况优先主体，再按阵亡轮次由早到晚选择。',
                '复活主体时，仅恢复所选主体。复活召唤物时，召唤物归环花暖所有，并受其对应召唤名额限制；首次转归环花暖计入其累计召唤数量。',
                '优先返回原位置；原位置无法容纳时，尝试施法者附近的空位。复活保留阵亡时记录的魔力、物品、形态及技能次数消耗，行动额度沿用已有记录。',
                '缺少符合条件的阵亡目标时，施放被拒绝，保留魔力与本场使用次数。召唤物名额及复活位置须满足要求。',
            ]
    elif name == '品尝蛋糕':
        inventory = [entry for entry in c.get('inventory', []) if entry.get('消费追加回复hp')]
        extra = text_value(inventory[0]['消费追加回复hp']) if inventory else DEFAULT
        count = sum(entry.get('count', 0) for entry in inventory)
        description = template.format(hp=value('回复hp'), extra=extra)
        target = '自身；品尝回复作用于食用者'
        paragraphs = [f'比哈米初始携带 {count} 个附带品尝回复的生命蛋糕。“最后的款待-恢复”分发的生命蛋糕同样附带此效果。蛋糕基础回复与品尝回复分别掷骰，每个蛋糕触发一次额外回复。']
    elif name.startswith('最后的款待-'):
        target = '己方存活或濒死主体'
        names = [item_names.get(effect.get('制造物模板'), text_value(effect.get('制造物模板', DEFAULT))) for effect in effects]
        description = template.format(items='与'.join(names))
        paragraphs = [f'{label}：{item_benefit(effect)}' for label, effect in zip(names, effects)]
        # The two shipped effects share allocation rules; keep each distinct
        # allocation visible if a data edit gives them different parameters.
        allocations = []
        for effect in effects:
            allocation = feast_paragraphs(effect)
            if allocation not in allocations:
                allocations.append(allocation)
                paragraphs.extend(allocation)
        paragraphs.extend(cake_use)
    else:
        target = '己方共享制造物池'
        description = template.format(count=value('制造个数'),
                                      item=item_names.get(e.get('制造物模板'), text_value(e.get('制造物模板', DEFAULT))),
                                      benefit=item_benefit(e))
        paragraphs = cake_use

    requirements.append(row('目标', target))
    sections = [_section('inputs', '施放要求', rows=requirements)]
    if paragraphs:
        sections.append(_section('effects', '技能效果', paragraphs))
    return dict(description=description, cost=' / '.join(costs), limits=target,
                timing=timing_text(s.get('timing', '2')).replace(' / ', '／'), facts=paragraphs,
                versions=[dict(id=v['id'], label=v['label'], overview=description,
                               sections=deepcopy(sections)) for v in VERSIONS])


def skill_article(s, c, summons, items):
    identity = (c['serial'], s['name'], s.get('phase', 0))
    overview = ARTICLES.get(identity)
    reviewed = overview is not None
    if not reviewed:
        overview = '自定义或新增技能：尚无经校对的机制正文；下列内容仅据声明数据列示，不推断扩展字段的战斗行为。'
    costs = spell_costs(s, c); inputs = spell_inputs(s, c); limits = limitation_rows(s)
    steps = settlement_steps(s, c)
    notes = rule_notes(s, c)
    unknown_spell = [k for k in s if k not in SPELL_LABELS and k not in ('effects','_disabled')]
    effects = [_effect_detail(e, s, c, i+1) for i,e in enumerate(s.get('effects', []))]
    templates = template_details(s, summons, items, c)
    unknown_effects = [k for e in effects for k in e['unverified_fields']]
    if unknown_spell or unknown_effects:
        notes.append('存在尚未核实的扩展字段：' + '、'.join(dict.fromkeys(unknown_spell+unknown_effects)) + '。仅列原值，不能据此确认已实现行为。')
    if reviewed and not overview.strip():
        raise ValueError(f'百科正文为空：{identity}')
    activation = [f"使用时机：{timing_text(s.get('timing', '2'))}。"]
    if s.get('phase') == 99: activation.append('当前战斗中不开放，效果声明不构成战斗使用承诺。')
    else: activation.append('发动前须处于可行动、可施法状态；目标、阶段、武器与专属资源仍须分别满足要求。')
    common = {
      'activation': _section('activation','触发与使用前提', activation),
      'inputs': _section('inputs','施放输入与目标', rows=inputs),
      'costs': _section('costs','费用与资源来源', ['费用表区分声明费用与专用分支的实际支付。正MP费用经百分比修正向下取整、再加固定修正后仍至少为1；SAN不足通常扣至零，不等同于取消施放。'], costs),
      'steps': _section('steps','结算顺序', steps, ordered=True),
      'effects': _section('effects','逐效果数值', effects=effects),
      'numbers': _section('numbers','动态公式与资源产出', rows=special_numbers(s,c)),
      'templates': _section('templates','召唤物与制造物', templates=templates),
      'limits': _section('limits','持续、次数与中断', rows=limits),
      'rules': _section('rules','特殊规则与实现边界', notes),
      'declared': _section('declared','技能声明补充', rows=field_rows({k:v for k,v in s.items() if k not in ('effects', 'name', 'category', 'timing', 'phase', '_disabled')}, SPELL_LABELS, c['serial'])),
    }
    orders = {
       'encyclopedia': ['activation','inputs','costs','steps','effects','numbers','templates','limits','rules','declared'],
       'procedure': ['inputs','activation','costs','steps','numbers','effects','templates','limits','rules','declared'],
       'reference': ['numbers','effects','costs','limits','inputs','activation','steps','templates','rules','declared'],
    }
    # Each version is complete. Editing style changes neither facts nor coverage.
    versions = []
    for version in VERSIONS:
        key = version['id']
        section_list = [deepcopy(common[k]) for k in orders[key] if any(common[k][part] for part in ('paragraphs','rows','effects','templates'))]
        if key == 'encyclopedia':
            intro = overview
        elif key == 'procedure':
            intro = f"使用【{s['name']}】时，先确认{timing_text(s.get('timing','2'))}入口和下列目标/投入，再按步骤结算。" + overview
            for section in section_list:
                if section['id'] == 'steps': section['title'] = '从起手到结算：逐步操作'
        else:
            intro = f"【{s['name']}】规则速查：" + overview
            for section in section_list:
                if section['id'] == 'effects': section['title'] = '效果索引与完整数值'
                if section['id'] == 'inputs': section['title'] = '可用输入索引'
        versions.append({'id':key, 'label':version['label'], 'overview':intro, 'sections':section_list})
    icon_order = ARTICLE_ORDER.get(c['serial'], [])
    legacy_facts = [e['title']+'：'+'；'.join(v['label']+' '+v['value'] for v in e['rows']) for e in effects]
    article = dict(id=json.dumps(identity, ensure_ascii=False, separators=(',',':')), name=s['name'],
                phase=s.get('phase',0), timing=timing_text(s.get('timing','2')), reviewed=reviewed,
                cost='；'.join(v['label']+'：'+v['value'] for v in costs),
                limits='；'.join(v['label']+'：'+v['value'] for v in limits),
                description=overview, facts=legacy_facts, versions=versions,
                audit={'spell_fields':list(s), 'unverified_fields':unknown_spell+unknown_effects,
                       'paths':AUDIT_PATHS['blood'] if s.get('blood_kind') else AUDIT_PATHS['common']},
                icon=(f"/ui/icons/{c['serial']}-s{icon_order.index(identity[1:])+1}.svg" if identity[1:] in icon_order else '/ui/icons/star.svg'))
    content = _y1_y2_content(s, c, summons)
    if content is not None:
        article.update(content)
    content = _y3_y4_content(s, c)
    if content is not None:
        article.update(content)
    content = _y7_y8_content(s, c, items)
    if content is not None:
        article.update(content)
    return article


def encyclopedia(character, summons, items):
    serial = character['serial']; profile = PROFILES.get(serial)
    traits = [{'name':n,'description':d} for n,d in profile[2]] if profile else []
    return dict(emblem=profile[0] if profile else 'star', color=profile[1] if profile else '#847fc0',
                role=' · '.join(t['name'] for t in traits) or character.get('role','自定义 · 魔法'),
                traits=traits, skills=[skill_article(s,character,summons,items) for s in character.get('spells',[])],
                versions=deepcopy(VERSIONS), passives=passive_articles(character),
                basic=f"普通攻击基础骰：{character.get('str_attrs',{}).get('伤害值',DEFAULT)}；斗殴 {character.get('attrs',{}).get('斗殴',DEFAULT)}；基础闪避 {character.get('attrs',{}).get('闪避',DEFAULT)}。db为体格伤害加成；阶段、武器及附魔可改写实际普攻。")


def passive_articles(c):
    rules = c.get('passive_rules', {}); rows = []
    if 'frost' in rules:
        r = rules['frost']; a = c['attrs']
        rows.append(f"冰冻被动：普攻命中+{a.get('冰冻命中层数', 1)}层，每段雪球命中+{a.get('冰冻雪球命中层数', a.get('冰冻命中层数', 1))}层；每轮先衰减{r['decay']}层，再结算雪带。每8层为一循环：1、5层各增加{r['vulnerability_pct']}%易伤；2层技能值−{r['skill_penalty']}并每轮受{r['dot']}伤害；3层闪避−{r['dodge_penalty']}、MOV−{r['move_penalty']}；4、8层触发下一轮MOV=0及{r['burst']}不可闪避伤害；7层追加2层同类减益；8层退还本循环1、5、7层的减益。")
        rows.append(f"每循环第{a.get('冰冻即时回复层数', 6)}层立即回复主人{r['instant_heal']} HP；第6层起于下一轮每轮回复{r['regen_hp']} HP与{r['regen_mp']} MP。" + ('跨循环减益和回复可叠加。' if a.get('冰冻跨循环叠加') else '持续回复仅保留一份。'))
    if 'cloak' in rules:
        r = rules['cloak']; rows.append(f"火鼠裘：每次结算消耗{r['mp_per_hit']} MP；伤害≤{r['immune_max']}归零，低于{r['middle_below']}固定为{r['middle_damage']}，更高伤害乘{r['high_multiplier']}并向下取整。魔力不足则不减免。")
    if 'cartridge' in rules:
        r = rules['cartridge']; rows.append(f"卡带规则：初始形态为最终幻想；切换共享{r['cooldown']}轮冷却。极限槽上限{r['limit_cap']}、每类合格事件每轮+{r['event_gain']}；金环上限{r['ring_cap']}，冲刺累计{r['dash_distance']}格可触发。其他己方主体首次濒死时极限槽另+2。")
    if 'phantom' in rules:
        r = rules['phantom']; rows.append('写作附加伤害：' + '；'.join(f'{label} {r[key]}' for key, label in [('4', '大成功'), ('3', '极难成功'), ('2', '困难成功'), ('1', '普通成功'), ('-2', '大失败')]) + '；普通失败为0。')
        rows.append(f"月能终结门槛：超过{rules['moon']['ultimate_threshold']}；弓形态短句额外消耗{rules['moon']['bow_extra_mp']} MP。")
    if c.get('blood_rules'):
        r = c['blood_rules']; rows.append(f"血术资源：初始{c['初始血液']}ml；守式每{r['armor_ml']}ml换{r['armor_per_unit']}护甲，上限{r['armor_cap']}；每{r['hp_to_ml']}ml不足可用1 HP补充；血球每{r['ball_ml']}ml回复{r['ball_heal']} HP；披风每{r['cape_ml']}ml回复{r['cape_mp']} MP。")
        rows.append(f"血武器持续{r['duration']}轮：血刃伤害{r['blade_damage']}，血弓伤害{r['bow_damage']}、射程{r['bow_range']}；血枪每支{r['spear_ml']}ml，上限{r['spear_cap']}支、耐久{r['spear_uses']}，近战{r['spear_melee']}、投掷{r['spear_throw']}、投掷射程{r['spear_range']}。")
        rows.append(f"污血战争基础半径{r['war_radius']}格，每额外1 MP半径+{r['war_extra_radius']}格，每具尸体最多{r['corpse_ml']}ml，每{r['chain_ml']}ml连锁；月斩基础{r['slash_base_ml']}ml，每额外{r['slash_extra_ml']}ml增加一段，每点伤害收血{r['slash_harvest_ml_per_hp']}ml、整次施放共享上限{r['slash_harvest_cap']}ml。")
        rows.append('月相：' + '；'.join(f'{name} {dice}' + ('，附带生命吸取' if drain else '') for name, dice, drain in r['moon_phases']) + '。')
    if c['serial'] == 'Y7':
        e = next((e for s in c.get('spells', []) for e in s.get('effects', []) if e.get('type') == 11), None)
        if e is None:
            return rows
        rows.extend(_y7_spirit_paragraphs(e))
    return rows
