"""基于大麦应用自动化抢票

# 特性
- [x] 选票失败时自动返回页面重选
- [x] 最大重试次数
- [ ] 选票场次限制
- [ ] 选票最高档位限制

# 设计约定
- 按页面(Page)组织界面逻辑
- 元素定位资源用枚举管理，定位方法通过 property 组织
- 抢票是低时延场景，因此必须尽可能减少元素操作次数
  - 必要的操作：确认页面加载、弹窗处理、页面跳转
  - 不必要的操作只有在直通逻辑无法实行时才执行：选择场次/票价/票数/用户
- 异常用作控制流（NoTicketsError 等），不在业务路径上返回错误码
- 不引入图像识别，不做完整的状态验证
- 关键路径上保留时间戳日志以备调试

# 已知局限
- 选票过程中按钮状态可能突变，导致点击后无反馈也不跳转
- uiautomator2 的 xpath 定位约 200-300ms，是耗时瓶颈
- 每次额外元素操作（如逐一检查场次有票）会拖慢路径，得不偿失
- 当前仅在理想环境下测试通过
"""

import logging
import re
import time
from abc import ABC, abstractmethod
from datetime import datetime
from enum import StrEnum
from typing import override

import click
import uiautomator2 as u2

logger = logging.getLogger(__name__)
_start_time: float = 0.0


def _log(msg: str):
    elapsed = time.monotonic() - _start_time if _start_time else 0.0
    logger.info("[%.3f] %s", elapsed, msg)


MAX_RETRIES = 10


class ParseTimeError(Exception):
    def __init__(self, sell_time: str) -> None:
        self.sell_time = sell_time
        super().__init__(f"解析时间错误: {sell_time}")


class StateError(Exception):
    """交互界面状态错误时触发"""


class MaxReretriesError(Exception):
    """超过最大重试次数时触发"""


class NoTicketsError(Exception):
    """当没有选票时触发"""


class _Page(ABC):
    name: str

    def __init__(self, device: u2.Device) -> None:
        self.device = device or u2.connect()

    @abstractmethod
    def wait(self, timeout: float | None = None) -> bool: ...

    def xpath(self, xpath: str):
        return self.device.xpath(xpath)

    def selector(self, **kwargs):
        return self.device(**kwargs)


def damai_id(id: str):
    return f"cn.damai:id/{id}"


class SellPage(_Page):
    name = "售票"

    class Resource(StrEnum):
        ConfirmButton = damai_id("trade_project_detail_purchase_status_bar_container_fl")
        TimePopup = damai_id("project_item_bottom_time_stagory")
        SellTimeText = damai_id("id_project_count_sell_time")

    @property
    def confirm_button(self):
        return self.selector(resourceId=self.Resource.ConfirmButton)

    @property
    def time_popup(self):
        return self.selector(resourceId=self.Resource.TimePopup)

    @override
    def wait(self, timeout: float | None = None) -> bool:
        return self.confirm_button.wait(exists=True)

    def wait_for_sell(self):
        """等待开售

        此方法可以接受大时延，因为抢票还未开始
        """
        _log("售票页: 等待开售")
        time_popup = self.time_popup
        if time_popup.exists():
            sell_time = time_popup.child_selector(resourceId=self.Resource.SellTimeText).get_text()

            def _parse_sell_time(sell_time: str):
                match_res = re.match(r"(\d{2})月(\d{2})日\W*(\d{2}):(\d{2}).*", sell_time)
                if match_res:
                    month, day, hour, minute = match_res.groups()
                    return datetime(
                        year=datetime.now().year,
                        month=int(month),
                        day=int(day),
                        hour=int(hour),
                        minute=int(minute),
                    )
                raise ParseTimeError(sell_time)

            sell_time = _parse_sell_time(sell_time)

            self._print_wait_for_sell(sell_time)
            selling = False
            while not selling:
                timeout = 1 if (sell_time - datetime.now()).seconds > 3 else 10
                self._print_waiting_for_sell(sell_time=sell_time)
                selling = self.time_popup.wait_gone(timeout=timeout)
                self.device.click(100, 50)  # 保持页面激活
        _log("售票页: 开售已开始")

    def confirm(self):
        _log("售票页: 点击确认按钮")
        self.confirm_button.click()
        return SelectPage(self.device)

    @property
    def title(self):
        return "".join(
            [
                ele.text
                for ele in self.xpath(
                    f'//*[@resource-id="{damai_id("concert_title_ll")}"]//android.widget.TextView'
                ).all()
            ]
        )

    @staticmethod
    def _print_wait_for_sell(sell_time):
        print(f"开售时间:\t{sell_time}")

    @staticmethod
    def _print_waiting_for_sell(sell_time: datetime):
        left_time = sell_time - datetime.now()
        hour = left_time.seconds // (60 * 60)
        minute = left_time.seconds % (60 * 60) // 60
        seconds = left_time.seconds % 60
        print(
            f"\r倒计时:\t{left_time.days}天 {hour:2}时 {minute:2}分 {seconds:2}秒",
            end="",
            flush=True,
        )


class SelectPage(_Page):
    name = "选票"
    perform: int
    ticket: int
    ticket_num: int

    class Resource(StrEnum):
        ConfirmButton = damai_id("btn_buy_view")
        NumLayout = damai_id("layout_num")
        PerformLayout = damai_id("layout_perform_view")
        PriceLayout = damai_id("layout_price")
        Item = damai_id("ll_perform_item")

        TicketNumber = damai_id("tv_num")
        PlusTicketButton = damai_id("img_jia")
        SubTicketButton = damai_id("img_jian")

        BackButton = damai_id("title_back_btn")

    def __init__(self, device: u2.Device) -> None:
        super().__init__(device)
        self.perform = 0
        self.ticket = 0
        self.ticket_num = 1

    def set_perform(self, index: int):
        self.perform = index

    def set_available_ticket(self, index: int):
        self.ticket = index

    def set_ticket_number(self, num: int):
        self.ticket_num = num

    @property
    def confirm_btn(self):
        return self.selector(resourceId=self.Resource.ConfirmButton)

    @property
    def available_performs(self):
        return self.xpath(
            f'//*[@resource-id="{self.Resource.PerformLayout}"]//*[@resource-id="{self.Resource.Item}"]'
            '[not(descendant::*[@text="无票"])]'
        ).all()

    @property
    def available_tickets(self):
        return self.xpath(
            f'//*[@resource-id="{self.Resource.PriceLayout}"]//*[@resource-id="{self.Resource.Item}"]'
            '[not(descendant::*[@text="缺货登记"])]'
        ).all()

    @property
    def current_ticket_number(self):
        num: str | None = self.selector(resourceId=self.Resource.TicketNumber).get_text()
        if num is None:
            raise StateError("还未选票")
        return int(num[:-1])

    @override
    def wait(self, timeout: float | None = None) -> bool:
        return bool(self.confirm_btn.wait(exists=True, timeout=timeout))

    def confirm(self):
        # FIXME: 缺少无票检测的逻辑
        if not self.selector(resourceId=self.Resource.NumLayout).exists():
            _log("选票页: 首次进入，需要选场次/票价/票数")
            self.choose_perform()
            self.choose_ticket()
            self.choose_ticket_num()
        else:
            _log("选票页: 直通模式，跳过选票直接确认")
        _log("选票页: 点击确认按钮")
        self.confirm_btn.click()
        return BuyPage(self.device)

    def choose_perform(self):
        performs = self.available_performs
        if not len(performs):
            raise NoTicketsError("所有场次都无票")
        performs_idx = self.perform if self.perform < len(performs) else 0
        _log(f"选票页: 选择场次 #{performs_idx}")
        performs[performs_idx].click()

    def choose_ticket(self):
        tickets = self.available_tickets
        if not len(tickets):
            raise NoTicketsError("暂无可售票")
        ticket_idx = self.ticket if self.perform < len(tickets) else 0
        _log(f"选票页: 选择票价档位 #{ticket_idx}")
        tickets[ticket_idx].click()

    def choose_ticket_num(self):
        current_num = self.current_ticket_number
        expect_num = self.ticket_num
        _log(f"选票页: 当前票数={current_num}, 期望票数={expect_num}")
        if expect_num > current_num:
            for _ in range(expect_num - current_num):
                self.selector(resourceId=self.Resource.PlusTicketButton).click()
        if current_num > expect_num:
            for _ in range(current_num - expect_num):
                self.selector(resourceId=self.Resource.SubTicketButton).click()
        # 无票判断
        current_num = self.current_ticket_number
        if expect_num != current_num:
            raise NoTicketsError(f"没有足够票额，当前票数: {current_num}")

    def back(self):
        _log("选票页: 返回上一页")
        self.selector(resourceId=self.Resource.BackButton).click()
        return SellPage(self.device)


class BuyPage(_Page):
    name = "购票"

    def __init__(self, device: u2.Device) -> None:
        super().__init__(device)
        self.need_choose_audiences = False
        self.audiences = []

    class Resource(StrEnum):
        AudiencesLayout = damai_id("recycler_main")
        NameText = damai_id("text_name")
        Checkbox = damai_id("checkbox")
        Dialog = damai_id("damai_theme_dialog_layout")
        DialogConfirmButton = damai_id("damai_theme_dialog_confirm_btn")
        BackButton = damai_id("title_back_btn")

    def set_need_choose_audiences(self, need_choose: bool):
        self.need_choose_audiences = need_choose

    def set_audiences(self, audiences: list[str]):
        self.audiences = audiences

    @property
    def confirm_button(self):
        return self.selector(text="立即提交")

    @property
    def chosen_users(self):
        if not len(self.audiences):
            raise StateError("未设置购票人")
        xpath = (
            f'//*[@resource-id="{self.Resource.AudiencesLayout}"]'
            + "//*["
            + " or ".join([f'@text="{name}"' for name in self.audiences])
            + "]"
            + f'/../*[@resource-id="{self.Resource.Checkbox}"]'
        )
        return self.xpath(xpath).all()

    @property
    def dialog(self):
        return self.selector(resourceId=self.Resource.Dialog)

    @override
    def wait(self, timeout: float | None = None) -> bool:
        return bool(self.confirm_button.wait(exists=True, timeout=timeout))

    def choose_audiences(self):
        checkboxs = self.chosen_users
        if len(checkboxs) != len(self.audiences):
            raise StateError("发现未知购票人，请先在应用中填好购票人信息")
        for checkbox in checkboxs:
            # FIXME: 这里点击可能会失败,暂不知道原因
            if checkbox.attrib["checked"] != "true":
                _log(f"购票页: 勾选购票人 {checkbox.attrib.get('text', '')}")
                checkbox.click()

    def skip_popup(self):
        dialog = self.dialog
        if dialog.exists():
            _log("购票页: 关闭弹窗")
            dialog.child_selector(resourceId=self.Resource.DialogConfirmButton).click()

    def confirm(self, max_retries=MAX_RETRIES):
        _log("购票页: 开始确认购票")
        self.choose_audiences()
        pay_page = PayPage(self.device)
        for i in range(max_retries):
            _log(f"购票页: 第{i + 1}次点击提交按钮")
            self.confirm_button.wait()
            self.confirm_button.click()
            for _ in range(max_retries):
                if pay_page.wait(0.3):
                    _log("购票页: 成功跳转到支付页")
                    return pay_page
                elif self.dialog.wait(exists=True, timeout=0.3):
                    _log("购票页: 检测到弹窗")
                    self.skip_popup()
                    break
        raise MaxReretriesError("购票失败, 超过最大重试次数")

    def back(self):
        _log("购票页: 返回上一页")
        self.selector(resourceId=self.Resource.BackButton).click()
        return SelectPage(self.device)


class PayPage(_Page):
    name = "支付"

    def wait(self, timeout: None | float = None) -> bool:
        exists = self.selector(resourceId="com.alipay.android.app:id/flybird_layout").wait(exists=True, timeout=timeout)
        if exists:
            _log("支付页: 已检测到支付界面")
        else:
            _log("支付页: 等待超时")
        return exists


def run(
    audiences: list[str], available_perform_idx: int = 0, available_ticket_idx: int = 0, max_retries: int = MAX_RETRIES
):
    global _start_time
    _start_time = time.monotonic()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    device = u2.connect()
    sell_page = SellPage(device=device)
    _log("程序启动，等待用户手动跳转到售票页面")
    print("售票程序已开启：请手动转跳到演唱会售票页面")
    sell_page.wait()
    print(f"===\t{sell_page.title}\t===")
    _log(f"当前演唱会: {sell_page.title}")
    # 等待起售
    select_page = _wait_for_sell(sell_page)
    buy_page = _select_perform_and_ticket(
        select_page,
        ticket_number=len(audiences),
        available_perform_idx=available_perform_idx,
        available_ticket_idx=available_ticket_idx,
    )
    while True:
        # 选票并跳转到购票界面
        buy_page.set_audiences(audiences)
        buy_page.wait()
        try:
            # 购票信息填入并尝试购买
            pay_page = buy_page.confirm(max_retries)
            break
        except MaxReretriesError:
            _log("购票页: 多次重试后失败，返回选票页重新选票")
            print("多次购买后失败，重新选票")
            select_page = buy_page.back()
            buy_page = _select_perform_and_ticket(
                select_page,
                ticket_number=len(audiences),
                available_perform_idx=available_perform_idx,
                available_ticket_idx=available_ticket_idx,
            )
    pay_page.wait()
    _log("支付页: 购买成功，等待用户手动支付")
    print("购买成功")


def _wait_for_sell(page: SellPage):
    _log("进入等待开售流程")
    page.wait()
    page.wait_for_sell()
    return page.confirm()


def _select_perform_and_ticket(
    select_page: SelectPage, ticket_number: int, available_perform_idx: int = 0, available_ticket_idx: int = 0
):
    _log("进入选票流程")
    while True:
        select_page.wait()
        select_page.set_perform(available_perform_idx)
        select_page.set_available_ticket(available_ticket_idx)
        select_page.set_ticket_number(ticket_number)
        try:
            return select_page.confirm()
        except NoTicketsError:
            _log("选票页: 无票，返回重试")
            print("未找到合适票型")
            sell_page = select_page.back()
            sell_page.wait()
            select_page = sell_page.confirm()


@click.command
@click.argument("audiences", nargs=-1, required=True)
@click.option("--perform", "-p", default=0, help="选择可选的演唱会场次序号")
@click.option("--ticket", "-t", default=0, help="选择可选的演唱会标记序号")
@click.option("--max-retries", "-m", default=MAX_RETRIES, help="选择最大重试次数")
def cli(audiences: tuple[str, ...], perform: int, ticket: int, max_retries):
    run(audiences=list(audiences), available_perform_idx=perform, available_ticket_idx=ticket, max_retries=max_retries)


if __name__ == "__main__":
    run(audiences=["龙相丞"])
