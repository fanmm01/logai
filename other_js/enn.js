// ==UserScript==
// @name         ENN成长
// @author       Fanyixu
// @version      1.1.0
// @description  使用 .enn/.en [技能名称][初始值] [次数][#] 来进行自动化多次成长检定（每次D100>当前技能值时技能+1D10）
// @timestamp    2026-07-19
// @license      MIT
// ==/UserScript==

if (!seal.ext.find('enn')) {
    const ext = seal.ext.new('enn', 'Fanyixu', '1.1.0');
    seal.ext.register(ext);

    const cmdEnn = seal.ext.newCmdItemInfo();
    cmdEnn.name = 'enn';
    cmdEnn.help = '使用 .enn/.en [技能名称][初始值] [次数][#] 来进行自动化多次成长检定\n'
                + '每次投掷D100，若结果大于当前技能值，则技能+1D10\n'
                + '技能名后可接数字作为初始值，次数后可加#隐藏详细过程\n'
                + '例: .en 图书馆使用75 500#\n'
                + '例: .enn 闪避 5\n'
                + '若省略次数参数，则仅查看当前技能值';

    cmdEnn.solve = (ctx, msg, cmdArgs) => {
        let rawArg1 = cmdArgs.getArgN(1);
        let rawArg2 = cmdArgs.getArgN(2);

        // 参数校验：必须提供技能名称
        if (!rawArg1) {
            seal.replyToSender(ctx, msg, '请指定技能名称。\n用法: .enn/.en [技能名称][初始值] [次数][#]\n例: .en 图书馆使用75 500#');
            return seal.ext.newCmdExecuteResult(true);
        }

        // ========== 解析技能名称和初始值 ==========
        let skillName;
        let inputInitial = null;

        if (/^\d+$/.test(rawArg1)) {
            // 全数字视为无效的技能名称
            seal.replyToSender(ctx, msg, '请提供有效的技能名称。\n用法: .enn/.en [技能名称][初始值] [次数][#]');
            return seal.ext.newCmdExecuteResult(true);
        }

        // 尝试从末尾提取数字作为初始值（如 "图书馆使用75" → name="图书馆使用", value=75）
        let matchName = rawArg1.match(/^(.+?)(\d+)$/);
        if (matchName) {
            skillName = matchName[1];
            inputInitial = parseInt(matchName[2]);
        } else {
            skillName = rawArg1;
        }

        // ========== 解析次数和 # 标志 ==========
        let times = null;
        let suppressDetail = false;

        if (rawArg2) {
            // 检测末尾的 # 标志
            if (rawArg2.endsWith('#')) {
                suppressDetail = true;
                rawArg2 = rawArg2.slice(0, -1);
            }
            // 解析剩余部分作为次数
            if (rawArg2) {
                times = parseInt(rawArg2);
                if (isNaN(times) || times <= 0) {
                    seal.replyToSender(ctx, msg, '次数必须是一个正整数！');
                    return seal.ext.newCmdExecuteResult(true);
                }
                if (times > 1000) {
                    seal.replyToSender(ctx, msg, '单次最多只能进行1000次成长检定！');
                    return seal.ext.newCmdExecuteResult(true);
                }
            }
        }

        // ========== 读取/初始化技能值 ==========
        let storageKey = 'ENN_SKILL_' + skillName;
        let currentSkill = 0;
        let stored = ext.storageGet(storageKey);
        if (stored) {
            currentSkill = parseInt(stored);
            if (isNaN(currentSkill)) currentSkill = 0;
        }

        // 如果提供了初始值，覆盖存储值（可用于重置技能）
        if (inputInitial !== null) {
            currentSkill = inputInitial;
            ext.storageSet(storageKey, currentSkill.toString());
        }

        // ========== 无次数 → 仅查看当前值 ==========
        if (times === null) {
            if (inputInitial !== null) {
                seal.replyToSender(ctx, msg, '【' + skillName + '】技能值已设定为: ' + currentSkill);
            } else {
                seal.replyToSender(ctx, msg, '【' + skillName + '】当前技能值: ' + currentSkill);
            }
            return seal.ext.newCmdExecuteResult(true);
        }

        // ========== 执行多次成长检定 ==========
        let initialSkill = currentSkill;
        let successCount = 0;
        let totalIncrease = 0;
        let logLines = [];

        for (let i = 0; i < times; i++) {
            let d100 = Math.floor(Math.random() * 100) + 1;
            let success = d100 > currentSkill;
            let increase = 0;

            if (success) {
                increase = Math.floor(Math.random() * 10) + 1;
                let oldSkill = currentSkill;
                currentSkill += increase;
                successCount++;
                totalIncrease += increase;
                logLines.push('第' + (i + 1) + '次: D100=' + d100 + ' > ' + oldSkill + ' ✅ 成功！+1D10=' + increase + '，技能值: ' + oldSkill + ' → ' + currentSkill);
            } else {
                logLines.push('第' + (i + 1) + '次: D100=' + d100 + ' ≤ ' + currentSkill + ' ❌ 失败，技能值保持: ' + currentSkill);
            }
        }

        // 保存新的技能值
        ext.storageSet(storageKey, currentSkill.toString());

        // ========== 构建输出 ==========
        let output = '【' + skillName + '】成长检定 ×' + times;
        output += '\n初始技能值: ' + initialSkill;
        output += '\n最终技能值: ' + currentSkill;
        output += '\n成功次数: ' + successCount + '/' + times;
        output += '\n总增长: +' + totalIncrease;

        if (!suppressDetail) {
            if (times <= 10) {
                output += '\n\n详细过程:';
                for (let j = 0; j < logLines.length; j++) {
                    output += '\n' + logLines[j];
                }
            } else {
                output += '\n\n（超过10次的详细过程已省略，仅显示汇总。在次数后加 # 可强制隐藏）';
            }
        }

        seal.replyToSender(ctx, msg, output);
        return seal.ext.newCmdExecuteResult(true);
    };

    // 同时注册 .enn 和 .en 两个命令名
    ext.cmdMap['enn'] = cmdEnn;
    ext.cmdMap['en'] = cmdEnn;
}
