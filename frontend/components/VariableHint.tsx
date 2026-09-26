import React from 'react';

/**
 * 各处的「变量替换」说明。
 *
 * 项目里有 4 处会做占位符替换，但界面上原本一处都没写：
 *
 *   1. 自动回复（关键词回复 / 默认回复）   {send_user_name} 等
 *   2. 商品专属回复                       同上 + {item_id}
 *   3. 卡密发货语                          {key} / {DELIVERY_CONTENT} 等
 *   4. API 卡密的请求参数                  {order_id} / {item_id} 等
 *
 * 不写说明的后果很具体：用户按直觉写出 `{key}` 这种不存在的变量时，它会被当成
 * 普通文字原样发出去，看起来就像「功能没生效」。所以这里把变量清单集中维护，
 * 各页面引用同一个组件，避免各写一份、改一处漏一处。
 */
export interface VariableDoc {
  /** 占位符写法，例如 {send_user_name} */
  name: string;
  /** 会被替换成什么 */
  desc: string;
}

/** 自动回复：关键词回复、默认回复 */
export const REPLY_VARIABLES: VariableDoc[] = [
  { name: '{send_user_name}', desc: '买家昵称' },
  { name: '{send_user_id}', desc: '买家 ID' },
  { name: '{send_message}', desc: '买家发来的原文' },
];

/** 商品专属回复：比自动回复多一个商品 ID */
export const ITEM_REPLY_VARIABLES: VariableDoc[] = [
  ...REPLY_VARIABLES,
  { name: '{item_id}', desc: '商品 ID' },
];

/** 卡密的「发货语」 */
export const DELIVERY_VARIABLES: VariableDoc[] = [
  { name: '{key}', desc: '卡密内容' },
  { name: '{DELIVERY_CONTENT}', desc: '卡密内容（历史写法，与 {key} 等价）' },
  { name: '{card_name}', desc: '卡密名称' },
  { name: '{item_title}', desc: '商品标题' },
  { name: '{buyer_id}', desc: '买家 ID' },
  { name: '{order_id}', desc: '订单号' },
];

/** API 卡密的请求参数（URL / headers / params 里都能用） */
export const API_CARD_VARIABLES: VariableDoc[] = [
  { name: '{order_id}', desc: '订单号' },
  { name: '{item_id}', desc: '商品 ID' },
  { name: '{buyer_id}', desc: '买家 ID' },
  { name: '{cookie_id}', desc: '账号 ID' },
  { name: '{spec_name}', desc: '规格名' },
  { name: '{spec_value}', desc: '规格值' },
  { name: '{order_amount}', desc: '订单金额' },
  { name: '{order_quantity}', desc: '购买数量' },
  { name: '{item_detail}', desc: '商品详情' },
];

interface VariableHintProps {
  variables: VariableDoc[];
  title?: string;
  /** 额外的规则说明 */
  note?: React.ReactNode;
}

const VariableHint: React.FC<VariableHintProps> = ({
  variables,
  title = '可用变量',
  note,
}) => (
  <div className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">
    <p className="font-bold">
      {title}
      <span className="ml-1 font-normal text-amber-700">
        （写在内容里的这些占位符，发送前会被自动替换成真实值）
      </span>
    </p>
    <ul className="mt-1 grid grid-cols-1 gap-x-4 gap-y-0.5 sm:grid-cols-2">
      {variables.map((variable) => (
        <li key={variable.name} className="flex flex-wrap items-baseline gap-x-2">
          <code className="rounded bg-white/70 px-1 font-mono text-[11px] text-amber-800">
            {variable.name}
          </code>
          <span className="text-amber-700">{variable.desc}</span>
        </li>
      ))}
    </ul>
    {note && <p className="mt-1 text-[11px] leading-5 text-amber-700">{note}</p>}
  </div>
);

export default VariableHint;
