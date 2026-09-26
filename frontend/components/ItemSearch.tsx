import React, { useCallback, useEffect, useState } from 'react';
import {
  Search,
  Loader2,
  ExternalLink,
  Store,
  MapPin,
  Heart,
  Clock,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react';
import { AccountDetail, SearchItem } from '../types';
import { getAccountDetails, searchItems } from '../services/api';
import { notify } from '../services/feedback';
import { EmptyState, NoticeBanner, PageHeader } from './ui';

// 后端搜索是"真的拉起浏览器去闲鱼搜"，所以这是个慢接口：
// 首次要冷启动 Chrome（十几秒起），因此这里所有文案都提前说明耗时，
// 免得用户以为卡死了。
const PAGE_SIZE_OPTIONS = [20, 40, 60];

const ItemSearch: React.FC = () => {
  const [accounts, setAccounts] = useState<AccountDetail[]>([]);
  const [accountId, setAccountId] = useState('');
  const [keyword, setKeyword] = useState('');
  const [pageSize, setPageSize] = useState(20);
  const [page, setPage] = useState(1);

  const [items, setItems] = useState<SearchItem[]>([]);
  const [total, setTotal] = useState(0);
  const [source, setSource] = useState('');
  const [isRealData, setIsRealData] = useState(true);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const [failedImages, setFailedImages] = useState<Set<string>>(new Set());

  useEffect(() => {
    getAccountDetails()
      .then((list) => {
        setAccounts(list);
        // 默认选第一个账号：多账号部署时后端要求"用哪个账号搜索就用哪份
        // 浏览器 profile 和 Cookie"，所以界面上必须让用户明确选一个。
        if (list.length > 0) setAccountId(list[0].id);
      })
      .catch(() => {
        /* 账号列表拉不到不影响搜索本身，后端会自己挑一个可用账号 */
      });
  }, []);

  const runSearch = useCallback(
    async (targetPage: number) => {
      const kw = keyword.trim();
      if (!kw) {
        notify('请输入搜索关键词', 'error');
        return;
      }
      setLoading(true);
      setSearched(true);
      try {
        const res = await searchItems({
          keyword: kw,
          page: targetPage,
          page_size: pageSize,
          cookie_id: accountId || undefined,
        });
        const list = res?.data || [];
        setItems(list);
        setTotal(res?.total || 0);
        setSource(res?.source || '');
        setIsRealData(res?.is_real_data !== false);
        setPage(targetPage);
        if (res?.error) {
          notify(res.error, 'error');
        } else if (list.length === 0) {
          notify('没有搜索到商品，换个关键词试试', 'info');
        }
      } catch (error) {
        setItems([]);
        setTotal(0);
        notify(error instanceof Error ? error.message : '搜索失败', 'error');
      } finally {
        setLoading(false);
      }
    },
    [keyword, pageSize, accountId],
  );

  const totalPages = pageSize > 0 ? Math.max(1, Math.ceil(total / pageSize)) : 1;
  const canPrev = page > 1 && !loading;
  const canNext = page < totalPages && !loading;

  return (
    <div>
      <PageHeader
        title="商品搜索"
        description="在闲鱼上按关键词搜商品，用于选品和比价。搜索会真实访问闲鱼，首次较慢。"
        icon={Search}
        badge={source ? <span className="status-badge status-badge-info">来源：{source}</span> : undefined}
      />

      {/* 搜索条件 */}
      <div className="ios-card mb-4 p-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
          <div className="flex-1">
            <label className="mb-2 block text-sm font-bold text-gray-700">关键词</label>
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
              <input
                type="text"
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') runSearch(1);
                }}
                placeholder="例如：显卡 / 儿童玩具 / 二手相机"
                className="ios-input w-full rounded-md py-2.5 pl-9 pr-3"
              />
            </div>
          </div>

          <div className="lg:w-64">
            <label className="mb-2 block text-sm font-bold text-gray-700">使用账号</label>
            <select
              value={accountId}
              onChange={(e) => setAccountId(e.target.value)}
              className="ios-input w-full rounded-md px-3 py-2.5"
            >
              {accounts.length === 0 && <option value="">（暂无可用账号）</option>}
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.nickname || account.remark || account.id}
                </option>
              ))}
            </select>
          </div>

          <div className="lg:w-32">
            <label className="mb-2 block text-sm font-bold text-gray-700">每页</label>
            <select
              value={pageSize}
              onChange={(e) => setPageSize(Number(e.target.value))}
              className="ios-input w-full rounded-md px-3 py-2.5"
            >
              {PAGE_SIZE_OPTIONS.map((size) => (
                <option key={size} value={size}>
                  {size} 条
                </option>
              ))}
            </select>
          </div>

          <button
            type="button"
            onClick={() => runSearch(1)}
            disabled={loading}
            className="ios-btn-primary flex items-center justify-center gap-2 rounded-md px-5 py-2.5 text-sm lg:w-32"
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
            搜索
          </button>
        </div>

        <p className="mt-2 text-xs leading-5 text-gray-400">
          提示：每个账号使用自己独立的浏览器 profile，不会互相串会话。首次搜索要冷启动浏览器，约 10~60 秒。
        </p>
      </div>

      {/* 回退数据提示：后端取不到真实结果时会返回模拟数据，别当真 */}
      {searched && !loading && !isRealData && (
        <div className="mb-4">
          <NoticeBanner type="warning">
            <b>本次不是真实搜索结果：</b>
            后端没能从闲鱼取到真实数据（通常是账号未登录、被风控或浏览器启动失败），
            返回的是用于占位的模拟数据。请检查账号状态后重试。
          </NoticeBanner>
        </div>
      )}

      {/* 加载中 */}
      {loading && (
        <div className="ios-card p-10 text-center">
          <Loader2 className="mx-auto h-6 w-6 animate-spin text-gray-400" />
          <p className="mt-3 text-sm font-bold text-gray-700">正在启动浏览器并搜索…</p>
          <p className="mt-1 text-xs text-gray-500">
            首次搜索需要冷启动 Chrome，通常 10~60 秒，请勿重复点击。
          </p>
        </div>
      )}

      {/* 结果 */}
      {!loading && items.length > 0 && (
        <>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm text-gray-500">
              共 <span className="font-bold text-gray-900">{total || items.length}</span> 条结果 · 第 {page} / {totalPages} 页
            </p>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => runSearch(page - 1)}
                disabled={!canPrev}
                className="ios-btn-secondary flex items-center gap-1 rounded-md px-3 py-1.5 text-xs disabled:opacity-50"
              >
                <ChevronLeft className="h-3.5 w-3.5" />
                上一页
              </button>
              <button
                type="button"
                onClick={() => runSearch(page + 1)}
                disabled={!canNext}
                className="ios-btn-secondary flex items-center gap-1 rounded-md px-3 py-1.5 text-xs disabled:opacity-50"
              >
                下一页
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {items.map((item, index) => {
              const image = item.main_image || '';
              const showImage = image && !failedImages.has(image);
              const tags = (item.tags || []).filter(Boolean);
              return (
                <article key={`${item.item_id}-${index}`} className="ios-card flex flex-col overflow-hidden">
                  <div className="relative aspect-square w-full overflow-hidden bg-gray-100">
                    {showImage ? (
                      <img
                        src={image}
                        alt={item.title}
                        loading="lazy"
                        onError={() => setFailedImages((prev) => new Set(prev).add(image))}
                        className="h-full w-full object-cover"
                      />
                    ) : (
                      <div className="flex h-full w-full items-center justify-center text-gray-300">
                        <Store className="h-8 w-8" />
                      </div>
                    )}
                    {typeof item.want_count === 'number' && item.want_count > 0 && (
                      <span className="absolute right-2 top-2 flex items-center gap-1 rounded-full bg-black/60 px-2 py-0.5 text-[11px] font-bold text-white">
                        <Heart className="h-3 w-3" />
                        {item.want_count} 人想要
                      </span>
                    )}
                  </div>

                  <div className="flex flex-1 flex-col gap-2 p-3">
                    <h3 className="line-clamp-2 text-sm font-bold leading-5 text-gray-900" title={item.title}>
                      {item.title || '（无标题）'}
                    </h3>

                    <p className="text-lg font-bold text-[#ff5000]">
                      ¥{item.price || '—'}
                    </p>

                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-gray-500">
                      {item.area && (
                        <span className="flex items-center gap-1">
                          <MapPin className="h-3 w-3" />
                          {item.area}
                        </span>
                      )}
                      {item.publish_time && item.publish_time !== '未知时间' && (
                        <span className="flex items-center gap-1">
                          <Clock className="h-3 w-3" />
                          {item.publish_time}
                        </span>
                      )}
                      {item.seller_name && (
                        <span className="flex items-center gap-1 truncate">
                          <Store className="h-3 w-3" />
                          {item.seller_name}
                        </span>
                      )}
                    </div>

                    {tags.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {tags.map((tag, tagIndex) => (
                          <span
                            key={`${item.item_id}-tag-${tagIndex}`}
                            className="rounded bg-gray-100 px-1.5 py-0.5 text-[10px] text-gray-600"
                          >
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}

                    <div className="mt-auto flex items-center justify-between gap-2 pt-1">
                      <span className="truncate font-mono text-[10px] text-gray-400" title={item.item_id}>
                        {item.item_id}
                      </span>
                      {item.item_url ? (
                        <a
                          href={item.item_url}
                          target="_blank"
                          rel="noreferrer"
                          className="flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-xs font-bold text-blue-600 hover:bg-blue-50"
                        >
                          打开
                          <ExternalLink className="h-3 w-3" />
                        </a>
                      ) : null}
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        </>
      )}

      {/* 空结果 */}
      {!loading && searched && items.length === 0 && (
        <EmptyState
          title="没有搜到商品"
          description="换个关键词，或确认所选账号处于正常登录状态后再试。"
          icon={Search}
        />
      )}

      {/* 初始态 */}
      {!loading && !searched && (
        <EmptyState
          title="输入关键词开始搜索"
          description="搜索会真实访问闲鱼，结果来自当前登录账号的视角，可用于选品与比价。"
          icon={Search}
        />
      )}
    </div>
  );
};

export default ItemSearch;
