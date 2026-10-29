/**
 * @file fortune.c
 * @brief 每日占星数据: xxapi(主) → freehoroscope(备) → 离线占卜引擎(兜底)
 *
 * 线上接口 (免密钥):
 *   https://v2.xxapi.cn/api/horoscope?type={sign}&time=today
 *   https://freehoroscopeapi.com/api/v1/get-horoscope/daily?sign={sign}
 */
#include "fortune.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "cJSON.h"
#include "esp_crt_bundle.h"
#include "esp_event.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "fortune";

#define XXAPI_URL "https://v2.xxapi.cn/api/horoscope?type=%s&time=today"
#define FREE_URL  "https://freehoroscopeapi.com/api/v1/get-horoscope/daily?sign=%s"
#define HTTP_BUF_SIZE 8192
#define HTTP_TIMEOUT_MS 15000

/* ------------------------------------------------------------------ */
/* 星座表                                                              */
/* ------------------------------------------------------------------ */

static const char *const kKeys[SIGN_COUNT] = {
    "aries", "taurus", "gemini", "cancer", "leo", "virgo",
    "libra", "scorpio", "sagittarius", "capricorn", "aquarius", "pisces"};
static const char *const kCn[SIGN_COUNT] = {
    "白羊座", "金牛座", "双子座", "巨蟹座", "狮子座", "处女座",
    "天秤座", "天蝎座", "射手座", "摩羯座", "水瓶座", "双鱼座"};

int fortune_sign_index(const char *name)
{
    if (!name || !name[0]) {
        return -1;
    }
    for (int i = 0; i < SIGN_COUNT; i++) {
        if (strcmp(name, kKeys[i]) == 0 || strcmp(name, kCn[i]) == 0) {
            return i;
        }
    }
    /* 繁体「獅子座 / 獅子」；用 UTF-8 字节避免给屏幕字库增加繁体字形。 */
    if (strcmp(name, "\xE7\x8D\x85\xE5\xAD\x90\xE5\xBA\xA7") == 0 ||
        strcmp(name, "\xE7\x8D\x85\xE5\xAD\x90") == 0) {
        return DISPLAY_SIGN_INDEX;
    }
    /* 中文简称: 「白羊」= 6 字节, 全名「白羊座」= 9 字节 */
    size_t n = strlen(name);
    if (n == 6) {
        for (int i = 0; i < SIGN_COUNT; i++) {
            if (strncmp(name, kCn[i], 6) == 0) {
                return i;
            }
        }
    }
    return -1;
}

const char *fortune_sign_key(int idx)
{
    return (idx >= 0 && idx < SIGN_COUNT) ? kKeys[idx] : kKeys[0];
}
const char *fortune_sign_cn(int idx)
{
    return (idx >= 0 && idx < SIGN_COUNT) ? kCn[idx] : kCn[0];
}

/* ------------------------------------------------------------------ */
/* 水印清理: 星o座o屋 / 星#座#屋 / 星8座8屋 / 星座屋 (仅在结尾)          */
/* ------------------------------------------------------------------ */

static bool eq3(const char *s, size_t p, size_t n, const char *ch3)
{
    return p + 3 <= n && memcmp(s + p, ch3, 3) == 0;
}

/* 水印: 星{0..4B}座{0..4B}屋 且「屋」在字符串结尾。例: 星o座o屋 / 星座屋 */
static void strip_watermark(char *s)
{
    size_t n = strlen(s);
    while (n > 0 && (s[n - 1] == ' ' || s[n - 1] == '\n' || s[n - 1] == '\r')) {
        s[--n] = 0;
    }
    if (n < 9) {
        return;
    }
    for (size_t i = 0; i + 9 <= n; i++) {
        if (!eq3(s, i, n, "\xE6\x98\x9F")) { /* 星 */
            continue;
        }
        for (size_t j = i + 3; j <= i + 7 && j + 3 <= n; j++) {
            if (!eq3(s, j, n, "\xE5\xBA\xA7")) { /* 座 */
                continue;
            }
            for (size_t k = j + 3; k <= j + 7 && k + 3 <= n; k++) {
                if (eq3(s, k, n, "\xE5\xB1\x8B") && k + 3 == n) { /* 屋 */
                    s[i] = 0;
                    return;
                }
            }
        }
    }
}

/* ------------------------------------------------------------------ */
/* HTTP GET (证书束)                                                    */
/* ------------------------------------------------------------------ */

typedef struct {
    char *buf;
    size_t cap;
    size_t len;
} acc_t;

static esp_err_t http_event(esp_http_client_event_t *ev)
{
    if (ev->event_id == HTTP_EVENT_ON_DATA) {
        acc_t *acc = (acc_t *)ev->user_data;
        if (acc && ev->data_len > 0) {
            size_t n = (size_t)ev->data_len;
            if (acc->len + n > acc->cap - 1) {
                n = acc->cap - 1 - acc->len;
            }
            if (n > 0) {
                memcpy(acc->buf + acc->len, ev->data, n);
                acc->len += n;
                acc->buf[acc->len] = 0;
            }
        }
    }
    return ESP_OK;
}

static bool http_get(const char *url, char *buf, size_t cap)
{
    acc_t acc = {.buf = buf, .cap = cap, .len = 0};
    buf[0] = 0;
    esp_http_client_config_t cfg = {
        .url = url,
        .event_handler = http_event,
        .user_data = &acc,
        .timeout_ms = HTTP_TIMEOUT_MS,
        .buffer_size = 2048,
        .crt_bundle_attach = esp_crt_bundle_attach,
    };
    esp_http_client_handle_t client = esp_http_client_init(&cfg);
    if (!client) {
        return false;
    }
    esp_err_t err = esp_http_client_perform(client);
    esp_http_client_cleanup(client);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "HTTP 失败 %s: %s", url, esp_err_to_name(err));
        return false;
    }
    return acc.len > 0;
}

/* ------------------------------------------------------------------ */
/* JSON 取值辅助                                                        */
/* ------------------------------------------------------------------ */

static void jstr(const cJSON *obj, const char *key, char *dst, size_t cap,
                 bool clean_wm)
{
    dst[0] = 0;
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(obj, key);
    if (cJSON_IsString(item) && item->valuestring) {
        snprintf(dst, cap, "%s", item->valuestring);
        if (clean_wm) {
            strip_watermark(dst);
        }
    }
}

static int jint(const cJSON *obj, const char *key)
{
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(obj, key);
    return cJSON_IsNumber(item) ? item->valueint : 0;
}

static void fill_sign(fortune_t *f, const char *sign_key)
{
    int idx = fortune_sign_index(sign_key);
    snprintf(f->sign_key, sizeof(f->sign_key), "%s", fortune_sign_key(idx));
    snprintf(f->sign_cn, sizeof(f->sign_cn), "%s", fortune_sign_cn(idx));
    snprintf(f->period, sizeof(f->period), "今日运势");
}

/* ------------------------------------------------------------------ */
/* 数据源 1: xxapi                                                     */
/* ------------------------------------------------------------------ */

static bool fetch_xxapi(const char *sign_key, fortune_t *f)
{
    char url[160];
    snprintf(url, sizeof(url), XXAPI_URL, sign_key);
    static char buf[HTTP_BUF_SIZE];
    if (!http_get(url, buf, sizeof(buf))) {
        return false;
    }
    cJSON *root = cJSON_ParseWithLength(buf, strlen(buf));
    if (!root) {
        ESP_LOGW(TAG, "xxapi JSON 解析失败");
        return false;
    }
    const cJSON *code = cJSON_GetObjectItemCaseSensitive(root, "code");
    const cJSON *data = cJSON_GetObjectItemCaseSensitive(root, "data");
    if (!cJSON_IsNumber(code) || code->valueint != 200 || !cJSON_IsObject(data)) {
        ESP_LOGW(TAG, "xxapi 返回异常: %.80s", buf);
        cJSON_Delete(root);
        return false;
    }

    const cJSON *name_item = cJSON_GetObjectItemCaseSensitive(data, "name");
    if (cJSON_IsString(name_item) && name_item->valuestring) {
        int returned_idx = fortune_sign_index(name_item->valuestring);
        if (returned_idx >= 0 && returned_idx != fortune_sign_index(sign_key)) {
            ESP_LOGW(TAG, "xxapi 返回星座与请求不符，尝试下一源");
            cJSON_Delete(root);
            return false;
        }
    }
    memset(f, 0, sizeof(*f));
    /* 始终标记请求的星座，不能因上游名称缺失/异常而显示成白羊座。 */
    fill_sign(f, sign_key);

    jstr(data, "type", f->period, sizeof(f->period), false);
    jstr(data, "shortcomment", f->comment, sizeof(f->comment), true);

    const cJSON *fortune_o = cJSON_GetObjectItemCaseSensitive(data, "fortune");
    const cJSON *index_o = cJSON_GetObjectItemCaseSensitive(data, "index");
    const cJSON *texts_o = cJSON_GetObjectItemCaseSensitive(data, "fortunetext");
    const cJSON *todo_o = cJSON_GetObjectItemCaseSensitive(data, "todo");

    f->score = jint(fortune_o, "all");
    jstr(index_o, "all", f->idx, sizeof(f->idx), false);
    jstr(index_o, "love", f->love, sizeof(f->love), false);
    jstr(index_o, "work", f->work, sizeof(f->work), false);
    jstr(index_o, "money", f->money, sizeof(f->money), false);
    jstr(index_o, "health", f->health, sizeof(f->health), false);
    jstr(texts_o, "all", f->overall, sizeof(f->overall), true);
    jstr(todo_o, "yi", f->yi, sizeof(f->yi), true);
    jstr(todo_o, "ji", f->ji, sizeof(f->ji), true);
    jstr(data, "luckycolor", f->color, sizeof(f->color), true);
    jstr(data, "luckynumber", f->number, sizeof(f->number), true);
    jstr(data, "luckyconstellation", f->buddy, sizeof(f->buddy), true);
    snprintf(f->tag, sizeof(f->tag), "xxapi");

    cJSON_Delete(root);
    if (f->overall[0] == 0) {
        return false;
    }
    ESP_LOGI(TAG, "xxapi ok: %s 综合=%d%% %s", f->sign_cn, f->score * 20, f->idx);
    return true;
}

/* ------------------------------------------------------------------ */
/* 数据源 2: freehoroscope (英文备用)                                   */
/* ------------------------------------------------------------------ */

static bool fetch_free(const char *sign_key, fortune_t *f)
{
    char url[160];
    snprintf(url, sizeof(url), FREE_URL, sign_key);
    static char buf[HTTP_BUF_SIZE];
    if (!http_get(url, buf, sizeof(buf))) {
        return false;
    }
    cJSON *root = cJSON_ParseWithLength(buf, strlen(buf));
    if (!root) {
        return false;
    }
    const cJSON *data = cJSON_GetObjectItemCaseSensitive(root, "data");
    const cJSON *hor = cJSON_GetObjectItemCaseSensitive(data, "horoscope");
    if (!cJSON_IsString(hor) || !hor->valuestring) {
        cJSON_Delete(root);
        return false;
    }
    memset(f, 0, sizeof(*f));
    fill_sign(f, sign_key);
    snprintf(f->overall, sizeof(f->overall), "%s", hor->valuestring);
    snprintf(f->tag, sizeof(f->tag), "en备用");
    cJSON_Delete(root);
    ESP_LOGI(TAG, "freehoroscope ok: %s", f->sign_cn);
    return true;
}

/* ------------------------------------------------------------------ */
/* 数据源 3: 离线占卜引擎 (兜底)                                        */
/* ------------------------------------------------------------------ */

static const char *const kOveralls[12] = {
    "今日星象平顺，把注意力放回手头最重要的一件事，节奏稳了，好运自然跟上。",
    "宇宙给你留了一扇小窗：先完成再完美，午后会迎来一个小小的转机。",
    "能量偏向内敛的一天，适合梳理与复盘；你默默做的准备，别人开始看见。",
    "今日贵人在侧，主动开口会得到回应；重要决定放在头脑最清醒的时段。",
    "别急着向所有人解释自己，留白也是力量；傍晚适合独处回血。",
    "小麻烦只是路过，不是停留；按自己的步调走，虚惊一场之后是坦途。",
    "灵感比执行力更旺的一天，先记下点子，再挑最容易的一件动手。",
    "人际磁场升温，一句真诚的夸赞能打开新局面；财务上宜守不宜攻。",
    "身体是今天最值得投资的资产，早睡早起，效率会给你惊喜。",
    "旧事重提不是为了翻篇，而是为了轻装；把结论写下来，心就定了。",
    "适合断舍离的一天，清理物品也是清理心绪，空间腾出来，机会才进得来。",
    "把大目标拆成三件小事，完成它们，今天就已经赢了。",
};
static const char *const kComments[12] = {
    "稳中求进", "厚积薄发", "顺水行舟", "静待花开", "蓄势待发", "温故知新",
    "以柔克刚", "水到渠成", "守正出奇", "步步为营", "轻装上阵", "曲径通幽"};
static const char *const kYi[12] = {
    "整理桌面与计划", "读几页闲书", "给绿植浇水", "出门散步晒太阳",
    "联系久未问候的朋友", "提早十分钟出门", "尝试一条新路线", "记录今天的小确幸",
    "把手机放远一点", "整理相册与文件", "听一张完整的专辑", "做一顿简单的饭"};
static const char *const kJi[12] = {
    "熬夜刷手机", "冲动消费", "临时改主意", "久坐不起", "替人做承诺",
    "空腹喝浓茶", "把话说太满", "临期赶工", "深夜做重大决定",
    "带情绪回消息", "忽略午休", "一次揽下太多事"};
static const char *const kColors[12] = {
    "黛蓝", "琥珀金", "松石绿", "樱粉", "月白", "朱砂",
    "墨黑", "草绿", "藕荷", "杏黄", "靛青", "银灰"};

static uint32_t rng_state;
static uint32_t rnd(void)
{
    /* xorshift32 */
    uint32_t x = rng_state;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    rng_state = x ? x : 0x9E3779B9u;
    return rng_state;
}
static int rnd_range(int lo, int hi) /* [lo, hi] */
{
    return lo + (int)(rnd() % (uint32_t)(hi - lo + 1));
}

void fortune_offline(const char *sign_key, int y, int m, int d, int salt,
                     const char *tag, fortune_t *out)
{
    memset(out, 0, sizeof(*out));
    fill_sign(out, sign_key);

    uint32_t seed = (uint32_t)(y * 10000 + m * 100 + d);
    seed ^= (uint32_t)(salt + 1) * 2654435761u;
    seed ^= (uint32_t)(fortune_sign_index(sign_key) + 1) * 40503u;
    rng_state = seed ? seed : 1u;
    rnd();

    snprintf(out->overall, sizeof(out->overall), "%s",
             kOveralls[rnd_range(0, 11)]);
    snprintf(out->comment, sizeof(out->comment), "%s",
             kComments[rnd_range(0, 11)]);
    snprintf(out->yi, sizeof(out->yi), "%s", kYi[rnd_range(0, 11)]);
    snprintf(out->ji, sizeof(out->ji), "%s", kJi[rnd_range(0, 11)]);
    snprintf(out->color, sizeof(out->color), "%s", kColors[rnd_range(0, 11)]);
    snprintf(out->number, sizeof(out->number), "%d", rnd_range(1, 99));
    int idx = fortune_sign_index(sign_key);
    int buddy = rnd_range(0, SIGN_COUNT - 2);
    if (buddy >= idx) {
        buddy++;
    }
    snprintf(out->buddy, sizeof(out->buddy), "%s", kCn[buddy]);

    out->score = rnd_range(3, 5);
    snprintf(out->idx, sizeof(out->idx), "%d%%",
             out->score * 20 + rnd_range(-6, 12));
    snprintf(out->love, sizeof(out->love), "%d%%",
             rnd_range(3, 5) * 20 + rnd_range(-6, 12));
    snprintf(out->work, sizeof(out->work), "%d%%",
             rnd_range(3, 5) * 20 + rnd_range(-6, 12));
    snprintf(out->money, sizeof(out->money), "%d%%",
             rnd_range(3, 5) * 20 + rnd_range(-6, 12));
    snprintf(out->health, sizeof(out->health), "%d%%",
             rnd_range(3, 5) * 20 + rnd_range(-6, 12));

    snprintf(out->tag, sizeof(out->tag), "%s", tag ? tag : "离线");
}

/* ------------------------------------------------------------------ */
/* 统一入口                                                            */
/* ------------------------------------------------------------------ */

void fortune_get(const char *sign_key, bool network_ok, fortune_t *out)
{
    if (network_ok) {
        if (fetch_xxapi(sign_key, out)) {
            return;
        }
        if (fetch_free(sign_key, out)) {
            return;
        }
    }
    time_t now = time(NULL);
    struct tm tm_v;
    localtime_r(&now, &tm_v);
    int y = tm_v.tm_year + 1900;
    if (now < 1600000000) {
        y = 2026; /* 时间未同步时给个稳定年份(种子用) */
    }
    /* salt 固定为 0: 离线结果必须「同日期 + 同星座 = 同一张卡」。
     * 旧实现用 (now & 0xFFFF) 作盐, 同一天不同时刻唤醒会换一套文案, 与
     * fortune.h / README 承诺的确定性不符。 */
    fortune_offline(sign_key, y, tm_v.tm_mon + 1, tm_v.tm_mday, 0,
                    network_ok ? "离线·降级" : "离线·无网", out);
    ESP_LOGW(TAG, "使用离线占卜引擎 (%s)", out->tag);
}
