"""
浏览器自动化处理模块
用于绕过 Vercel Security Checkpoint 等安全验证
"""

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
import time
import json


class BrowserSession:
    """处理需要浏览器验证的会话"""

    def __init__(self, config):
        self.config = config
        self.clash_proxy = config['clash']['local_proxy']
        self.api_base = config.get('api_base', 'https://megallm.io')
        self.headless = config.get('browser', {}).get('headless', True)
        self.timeout = config.get('browser', {}).get('timeout', 30000)

    def get_verified_session(self, proxy_name=None):
        """
        通过浏览器完成验证并返回 cookies

        Args:
            proxy_name: 可选，使用的代理节点名称（用于日志）

        Returns:
            dict: 验证后的 cookies 字典
        """
        log_prefix = f"[{proxy_name}] " if proxy_name else ""
        print(f"{log_prefix}🌐 启动浏览器进行安全验证...")

        with sync_playwright() as p:
            browser = None
            try:
                # 启动浏览器
                browser = p.chromium.launch(
                    headless=self.headless,
                    proxy={"server": self.clash_proxy},
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--disable-dev-shm-usage',
                        '--no-sandbox'
                    ]
                )

                # 创建浏览器上下文
                context = browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    locale='en-US',
                    timezone_id='America/New_York'
                )

                # 注入反检测脚本
                context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });
                """)

                page = context.new_page()

                print(f"{log_prefix}📡 正在访问 {self.api_base}...")

                # 访问首页触发验证
                page.goto(self.api_base, wait_until='domcontentloaded', timeout=self.timeout)

                # 等待验证完成的多种策略
                verified = False
                start_time = time.time()
                max_wait = self.timeout / 1000  # 转换为秒

                while time.time() - start_time < max_wait:
                    current_url = page.url

                    # 检查是否还在 checkpoint 页面
                    if 'checkpoint' not in current_url.lower():
                        print(f"{log_prefix}✅ 安全验证通过！")
                        verified = True
                        break

                    # 检查页面内容是否有验证成功的标志
                    try:
                        # 检查是否加载了正常页面内容
                        page.wait_for_selector('body', timeout=1000)
                        body_text = page.text_content('body')

                        if body_text and 'verifying your browser' not in body_text.lower():
                            print(f"{log_prefix}✅ 页面内容验证通过！")
                            verified = True
                            break
                    except:
                        pass

                    # 等待一小段时间再检查
                    time.sleep(0.5)

                if not verified:
                    print(f"{log_prefix}⚠️  验证超时，尝试继续获取 cookies...")

                # 额外等待确保所有 cookies 设置完成
                time.sleep(2)

                # 获取 cookies
                cookies = context.cookies()

                if not cookies:
                    print(f"{log_prefix}⚠️  未获取到任何 cookies")
                    return {}

                # 转换为 requests 可用格式
                session_cookies = {c['name']: c['value'] for c in cookies}

                print(f"{log_prefix}🍪 成功获取 {len(session_cookies)} 个 cookies")

                # 打印关键 cookies（调试用）
                important_cookies = ['_vercel_jwt', '__vercel_live_token', 'vercel-checkpoint']
                for key in important_cookies:
                    if key in session_cookies:
                        print(f"{log_prefix}   - {key}: {session_cookies[key][:20]}...")

                return session_cookies

            except PlaywrightTimeout as e:
                print(f"{log_prefix}❌ 浏览器操作超时: {e}")
                return {}
            except Exception as e:
                print(f"{log_prefix}❌ 浏览器验证失败: {e}")
                import traceback
                traceback.print_exc()
                return {}
            finally:
                if browser:
                    browser.close()

    def test_cookies(self, cookies):
        """
        测试 cookies 是否有效

        Args:
            cookies: cookies 字典

        Returns:
            bool: cookies 是否有效
        """
        if not cookies:
            return False

        print("🧪 测试 cookies 有效性...")

        with sync_playwright() as p:
            browser = None
            try:
                browser = p.chromium.launch(
                    headless=True,
                    proxy={"server": self.clash_proxy}
                )

                context = browser.new_context()

                # 设置 cookies
                cookie_list = [
                    {
                        'name': name,
                        'value': value,
                        'domain': self.api_base.replace('https://', '').replace('http://', ''),
                        'path': '/'
                    }
                    for name, value in cookies.items()
                ]
                context.add_cookies(cookie_list)

                page = context.new_page()

                # 访问页面
                page.goto(self.api_base, wait_until='domcontentloaded', timeout=10000)

                # 检查是否被重定向到 checkpoint
                if 'checkpoint' in page.url.lower():
                    print("❌ Cookies 已失效（被重定向到验证页面）")
                    return False

                print("✅ Cookies 有效")
                return True

            except Exception as e:
                print(f"❌ Cookies 测试失败: {e}")
                return False
            finally:
                if browser:
                    browser.close()


class CookieManager:
    """Cookie 管理器，用于缓存和复用 cookies"""

    def __init__(self, cache_file='browser_cookies.json'):
        self.cache_file = cache_file
        self.cookies_cache = self._load_cache()

    def _load_cache(self):
        """从文件加载缓存的 cookies"""
        try:
            with open(self.cache_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_cache(self):
        """保存 cookies 到文件"""
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cookies_cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️  保存 cookies 缓存失败: {e}")

    def get_cookies(self, proxy_name=None):
        """获取指定代理的 cookies"""
        key = proxy_name or 'default'
        return self.cookies_cache.get(key, {})

    def set_cookies(self, cookies, proxy_name=None):
        """设置指定代理的 cookies"""
        key = proxy_name or 'default'
        self.cookies_cache[key] = {
            'cookies': cookies,
            'timestamp': time.time()
        }
        self._save_cache()

    def is_expired(self, proxy_name=None, max_age=3600):
        """检查 cookies 是否过期（默认 1 小时）"""
        key = proxy_name or 'default'
        if key not in self.cookies_cache:
            return True

        cache_data = self.cookies_cache[key]
        age = time.time() - cache_data.get('timestamp', 0)
        return age > max_age

    def clear_cookies(self, proxy_name=None):
        """清除指定代理的 cookies"""
        key = proxy_name or 'default'
        if key in self.cookies_cache:
            del self.cookies_cache[key]
            self._save_cache()


if __name__ == '__main__':
    # 测试代码
    import sys

    test_config = {
        'api_base': 'https://megallm.io',
        'clash': {
            'local_proxy': 'http://127.0.0.1:7897'
        },
        'browser': {
            'headless': False,  # 测试时显示浏览器
            'timeout': 30000
        }
    }

    print("=" * 50)
    print("浏览器验证模块测试")
    print("=" * 50)

    browser_session = BrowserSession(test_config)
    cookies = browser_session.get_verified_session()

    if cookies:
        print(f"\n✅ 成功获取 cookies: {list(cookies.keys())}")

        # 测试 cookies 有效性
        is_valid = browser_session.test_cookies(cookies)
        print(f"\nCookies 有效性: {'✅ 有效' if is_valid else '❌ 无效'}")
    else:
        print("\n❌ 未能获取 cookies")
        sys.exit(1)
