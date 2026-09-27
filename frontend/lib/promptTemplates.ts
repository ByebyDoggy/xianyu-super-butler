/**
 * 系统提示词模板的前端解析 / 拼装。
 *
 * 存储只有一个字段 `custom_prompts`（后端也用它），历史上有两种格式：
 *   - 纯文本                        → 作为「卖家补充规则」追加在内置角色设定之后
 *   - JSON（意图键 + extra_rules）  → 该意图的角色设定整体替换内置默认，外加补充规则
 *
 * 界面要把它拆成「每个意图一个模板 + 一块补充规则」，保存时再合回去。
 * 这套转换集中放在这里，避免拆装逻辑散落在组件里、两边写法不一致。
 */

export interface PromptIntent {
  key: string;
  label: string;
  hint: string;
}

export const PROMPT_INTENTS: PromptIntent[] = [
  { key: 'default', label: '通用', hint: '普通咨询：商品介绍、物流、售后等' },
  { key: 'price', label: '议价', hint: '买家砍价时使用' },
  { key: 'tech', label: '技术 / 产品', hint: '产品功能、使用方法类提问' },
];

export interface ParsedCustomPrompts {
  /** 只包含「被自定义过」的意图；没有的键表示用内置默认 */
  templates: Record<string, string>;
  /** 卖家补充规则（追加） */
  extraRules: string;
}

export function parseCustomPrompts(raw: string): ParsedCustomPrompts {
  const text = (raw || '').trim();
  if (!text) return { templates: {}, extraRules: '' };

  try {
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      const record = parsed as Record<string, unknown>;
      const templates: Record<string, string> = {};
      for (const { key } of PROMPT_INTENTS) {
        const value = record[key];
        if (typeof value === 'string' && value.trim()) templates[key] = value;
      }
      const extra = record.extra_rules;
      return { templates, extraRules: typeof extra === 'string' ? extra : '' };
    }
  } catch {
    // 不是 JSON：老格式，整段纯文本就是补充规则
  }

  return { templates: {}, extraRules: text };
}

/**
 * 把「每个意图的模板 + 补充规则」合成 custom_prompts。
 *
 * - 某个意图的模板与内置默认一致（或留空）→ 不写进库里，表示「用内置默认」，
 *   这样以后内置模板升级了，这个账号还能跟着升级，而不是被一份旧副本钉住。
 * - 只有补充规则时存纯文本，与历史数据保持同一种格式，库里也更好读。
 */
export function composeCustomPrompts(
  templates: Record<string, string>,
  extraRules: string,
  builtins: Record<string, string>,
): string {
  const overrides: Record<string, string> = {};
  for (const { key } of PROMPT_INTENTS) {
    const value = (templates[key] || '').trim();
    if (!value || value === (builtins[key] || '').trim()) continue;
    overrides[key] = value;
  }

  const extra = (extraRules || '').trim();

  if (Object.keys(overrides).length === 0) {
    return extra;
  }

  const payload: Record<string, string> = { ...overrides };
  if (extra) payload.extra_rules = extra;
  return JSON.stringify(payload);
}

/** 该意图当前是否在用内置默认 */
export function isUsingBuiltin(
  templates: Record<string, string>,
  builtins: Record<string, string>,
  key: string,
): boolean {
  const value = (templates[key] || '').trim();
  return !value || value === (builtins[key] || '').trim();
}
