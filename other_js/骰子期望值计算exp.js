// ==UserScript==
// @name         期望值计算
// @author       SzzRain
// @version      1.1.0
// @description  仅支持基础骰子表达式（即 xdx+xdx）的期望值计算，使用 .exp help 查看帮助
// @timestamp    1745418723
// @license      MIT
// @homepageURL  https://github.com/Szzrain
// ==/UserScript==

if (!seal.ext.find('exp')) {
    const ext = seal.ext.new('exp', 'SzzRain', '1.1.0');
    seal.ext.register(ext);
    const cmdexp = seal.ext.newCmdItemInfo();
    cmdexp.name = 'exp';
    cmdexp.help = '用.exp 表达式 来计算骰子的期望值，支持 + - * / d ?: 括号和比较运算\n例: .exp 2d6+1d8-2, .exp 3d6*2, .exp 1d6>3?2d8:1d4\n如果你想查看帮助，请输入 .exp help';
    cmdexp.solve = (ctx, msg, cmdArgs) => {
        let val = cmdArgs.getArgN(1);
        switch (val) {
            case 'help': {
                const ret = seal.ext.newCmdExecuteResult(true);
                ret.showHelp = true;
                return ret;
            }
            default: {
                try {
                    // 解析表达式并计算期望值
                    const result = calculateExpression(val);
                    seal.replyToSender(ctx, msg, `表达式 ${val} 的期望值为 ${result}`);
                } catch (e) {
                    seal.replyToSender(ctx, msg, `解析表达式时出错: ${e.message}`);
                }
                return seal.ext.newCmdExecuteResult(true);
            }
        }
    };
    ext.cmdMap['exp'] = cmdexp;
}

// Token 类型常量
var TYPE_NUMBER = "NUMBER";
var TYPE_D = "D";
var TYPE_PLUS = "PLUS";
var TYPE_MINUS = "MINUS";
var TYPE_MUL = "MUL";
var TYPE_DIV = "DIV";
var TYPE_QUESTION = "QUESTION";
var TYPE_COLON = "COLON";
var TYPE_LPAREN = "LPAREN";
var TYPE_RPAREN = "RPAREN";
var TYPE_GT = "GT";
var TYPE_LT = "LT";
var TYPE_GTE = "GTE";
var TYPE_LTE = "LTE";
var TYPE_EQ = "EQ";
var TYPE_NEQ = "NEQ";
var TYPE_EOF = "EOF";

// 词法分析器：将表达式字符串转为 Token 数组
function tokenize(expression) {
    var tokens = [];
    var i = 0;
    while (i < expression.length) {
        var ch = expression[i];
        // 跳过空白
        if (ch === " " || ch === "\t" || ch === "\n" || ch === "\r") {
            i++;
            continue;
        }
        // 数字
        if (ch >= "0" && ch <= "9") {
            var start = i;
            while (i < expression.length && expression[i] >= "0" && expression[i] <= "9") {
                i++;
            }
            tokens.push({ type: TYPE_NUMBER, value: expression.slice(start, i) });
            continue;
        }
        // 骰子运算符
        if (ch === "d" || ch === "D") {
            tokens.push({ type: TYPE_D });
            i++;
            continue;
        }
        // 算术运算符
        if (ch === "+") {
            tokens.push({ type: TYPE_PLUS });
            i++;
            continue;
        }
        if (ch === "-") {
            tokens.push({ type: TYPE_MINUS });
            i++;
            continue;
        }
        if (ch === "*") {
            tokens.push({ type: TYPE_MUL });
            i++;
            continue;
        }
        if (ch === "/") {
            tokens.push({ type: TYPE_DIV });
            i++;
            continue;
        }
        // 三元运算符
        if (ch === "?") {
            tokens.push({ type: TYPE_QUESTION });
            i++;
            continue;
        }
        if (ch === ":") {
            tokens.push({ type: TYPE_COLON });
            i++;
            continue;
        }
        // 括号
        if (ch === "(") {
            tokens.push({ type: TYPE_LPAREN });
            i++;
            continue;
        }
        if (ch === ")") {
            tokens.push({ type: TYPE_RPAREN });
            i++;
            continue;
        }
        // 比较运算符
        if (ch === ">") {
            if (i + 1 < expression.length && expression[i + 1] === "=") {
                tokens.push({ type: TYPE_GTE });
                i += 2;
            } else {
                tokens.push({ type: TYPE_GT });
                i++;
            }
            continue;
        }
        if (ch === "<") {
            if (i + 1 < expression.length && expression[i + 1] === "=") {
                tokens.push({ type: TYPE_LTE });
                i += 2;
            } else {
                tokens.push({ type: TYPE_LT });
                i++;
            }
            continue;
        }
        if (ch === "=") {
            if (i + 1 < expression.length && expression[i + 1] === "=") {
                tokens.push({ type: TYPE_EQ });
                i += 2;
            } else {
                throw new Error("意外的字符 '='，您是否想用 '=='？");
            }
            continue;
        }
        if (ch === "!") {
            if (i + 1 < expression.length && expression[i + 1] === "=") {
                tokens.push({ type: TYPE_NEQ });
                i += 2;
            } else {
                throw new Error("意外的字符 '!'，您是否想用 '!='？");
            }
            continue;
        }
        throw new Error("意外的字符 '" + ch + "'");
    }
    tokens.push({ type: TYPE_EOF });
    return tokens;
}

// 递归下降解析器
function parse(tokens) {
    var pos = 0;

    function peek() {
        return tokens[pos];
    }

    function consume() {
        return tokens[pos++];
    }

    function expect(type) {
        var tok = consume();
        if (tok.type !== type) {
            throw new Error("期望 " + type + " 但得到了 " + tok.type);
        }
        return tok;
    }

    // 三元表达式（最低优先级，右结合）
    function parseTernary() {
        var cond = parseComparison();
        if (peek().type === TYPE_QUESTION) {
            consume(); // 吃掉 '?'
            var trueVal = parseTernary();
            expect(TYPE_COLON);
            var falseVal = parseTernary();
            return cond !== 0 ? trueVal : falseVal;
        }
        return cond;
    }

    // 比较运算
    function parseComparison() {
        var left = parseAdditive();
        while (true) {
            var tok = peek();
            if (tok.type === TYPE_GT || tok.type === TYPE_LT ||
                tok.type === TYPE_GTE || tok.type === TYPE_LTE ||
                tok.type === TYPE_EQ || tok.type === TYPE_NEQ) {
                var op = consume();
                var right = parseAdditive();
                switch (op.type) {
                    case TYPE_GT:  left = left > right ? 1 : 0; break;
                    case TYPE_LT:  left = left < right ? 1 : 0; break;
                    case TYPE_GTE: left = left >= right ? 1 : 0; break;
                    case TYPE_LTE: left = left <= right ? 1 : 0; break;
                    case TYPE_EQ:  left = left === right ? 1 : 0; break;
                    case TYPE_NEQ: left = left !== right ? 1 : 0; break;
                }
            } else {
                break;
            }
        }
        return left;
    }

    // 加减
    function parseAdditive() {
        var left = parseMultiplicative();
        while (true) {
            var tok = peek();
            if (tok.type === TYPE_PLUS) {
                consume();
                left = left + parseMultiplicative();
            } else if (tok.type === TYPE_MINUS) {
                consume();
                left = left - parseMultiplicative();
            } else {
                break;
            }
        }
        return left;
    }

    // 乘除
    function parseMultiplicative() {
        var left = parseDice();
        while (true) {
            var tok = peek();
            if (tok.type === TYPE_MUL) {
                consume();
                left = left * parseDice();
            } else if (tok.type === TYPE_DIV) {
                consume();
                var right = parseDice();
                if (right === 0) {
                    throw new Error("除零错误");
                }
                left = left / right;
            } else {
                break;
            }
        }
        return left;
    }

    // 骰子运算 NdM: 期望值 = N * (M+1) / 2
    function parseDice() {
        var left = parseUnary();
        while (peek().type === TYPE_D) {
            consume(); // 吃掉 'd'
            var right = parseUnary();
            left = left * (right + 1) / 2;
        }
        return left;
    }

    // 一元运算符
    function parseUnary() {
        if (peek().type === TYPE_MINUS) {
            consume();
            return -parseUnary();
        }
        if (peek().type === TYPE_PLUS) {
            consume();
            return parseUnary();
        }
        return parsePrimary();
    }

    // 基础单元：数字、括号、隐式骰子
    function parsePrimary() {
        var tok = peek();
        if (tok.type === TYPE_NUMBER) {
            consume();
            return parseFloat(tok.value);
        }
        if (tok.type === TYPE_LPAREN) {
            consume(); // 吃掉 '('
            var val = parseTernary();
            expect(TYPE_RPAREN);
            return val;
        }
        // 隐式骰子: "d6" 等价于 "1d6"
        if (tok.type === TYPE_D) {
            consume(); // 吃掉 'd'
            var right = parseUnary();
            return (right + 1) / 2;
        }
        throw new Error("意外的 Token: " + tok.type);
    }

    // 入口
    var result = parseTernary();
    if (peek().type !== TYPE_EOF) {
        throw new Error("表达式解析完毕后仍有未处理的 Token: " + peek().type);
    }
    return result;
}

// 计算复杂骰子表达式的期望值
function calculateExpression(expression) {
    if (!expression || expression.trim() === "") {
        throw new Error("表达式不能为空");
    }
    var tokens = tokenize(expression);
    var result = parse(tokens);
    // 避免浮点精度问题
    return Math.round(result * 1e10) / 1e10;
}