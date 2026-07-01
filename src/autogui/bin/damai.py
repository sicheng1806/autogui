"""基于大麦应用自动化抢票

# 实现方式
- 通过 uiautomator2 在售票界面执行操作
- 界面有：售票 -> 选票 -> 购票 -> 支付
- 核心逻辑为:
  - 售票界面:等待抢票开始
  - 选票界面:选择场次、票价和购票数
  - 购票界面:选择购票人跳转到支付界面
  - 支付界面:只需确认跳转到支付界面即可，后续操作有用户执行
- 程序按界面(Page)进行组织，界面元素定位涉及到的资源按枚举组织，定位元素的方法通过property组织，具体页面逻辑通过方法组织
- 由于抢票是是耗时要求极低的场景，因此必须经可能减少元素操作，在一定情况下允许绕开程序逻辑，进行直通式操作
- 必要的元素操作:
  - 确认页面是否已经加载
  - 弹窗处理
  - 跳转到下一个窗口的操作
- 不必要的操作只有在直通逻辑无法实行的情况下执行
  - 选择场次
  - 选择票价
  - 选择票数
  - 选择用户
  - 确认当前购票总价
- 什么不做:
  - 不引入基于图像识别的操作，即使会影响通用性
  - 不做完全逻辑可靠的交互过程，最小化耗时
  - 不做日志，小脚本没必要，打印信息通过页面的_print_xx方法
"""

import re
from abc import ABC, abstractmethod
from datetime import datetime
from enum import StrEnum
from typing import override

import click
import uiautomator2 as u2


class ParseTimeError(Exception):
    def __init__(self, sell_time: str) -> None:
        self.sell_time = sell_time
        super().__init__(f"解析时间错误: {sell_time}")


class StateError(Exception):
    """交互界面状态错误时触发"""

    pass


class _Page(ABC):
    name: str

    def __init__(self, device: u2.Device) -> None:
        self.device = device or u2.connect()
        print(f"进入{self.name}界面")

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

    def __init__(self, device: u2.Device) -> None:
        super().__init__(device)
        print("请跳转到演唱会页面以开始抢票")

    class Resource(StrEnum):
        ConfirmButton = damai_id("trade_project_detail_purchase_status_bar_container_fl")
        TimePopup = damai_id("project_item_bottom_time_stagory")
        SellTimeText = damai_id("id_project_count_sell_time")
        Title = damai_id("")

    @property
    def confirm_button(self):
        return self.selector(resourceId=self.Resource.ConfirmButton)

    @property
    def time_popup(self):
        return self.selector(resourceId=self.Resource.TimePopup)

    @override
    def wait(self, timeout: float | None = None) -> bool:
        res = self.confirm_button.wait(exists=True)
        if res:
            self._print_title()
            return True
        return False

    def wait_for_sell(self):
        """等待开售

        此方法可以接受大时延，因为抢票还未开始
        """
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

    def confirm(self):
        self.confirm_button.click()
        return SelectPage(self.device)

    def _print_title(self):
        title = "".join(
            [
                ele.text
                for ele in self.xpath(
                    f'//*[@resource-id="{damai_id("concert_title_ll")}"]//android.widget.TextView'
                ).all()
            ]
        )

        print(f"演唱会:\t{title}")

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
        ).all()

    @property
    def available_tickets(self):
        return self.xpath(
            f'//*[@resource-id="{self.Resource.PriceLayout}"]//*[@resource-id="{self.Resource.Item}"]'
            f'[not(descendant::*[@text="缺货登记"])]'
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
        if not self.selector(resourceId=self.Resource.NumLayout).exists():
            self.choose_perform()
            self.choose_ticket()
            self.choose_ticket_num()
        self.confirm_btn.click()
        return BuyPage(self.device)

    def choose_perform(self):
        performs = self.available_performs
        performs_idx = self.perform if len(performs) <= self.perform + 1 else 0
        performs[performs_idx].click()

    def choose_ticket(self):
        tickets = self.available_tickets
        if not len(tickets):
            raise StateError("暂无可售票")
        ticket_idx = self.ticket if len(tickets) <= self.perform + 1 else 0
        tickets[ticket_idx].click()

    def choose_ticket_num(self):
        current_num = self.current_ticket_number
        expect_num = self.ticket_num
        if expect_num > current_num:
            for _ in range(expect_num - current_num):
                self.selector(resourceId=self.Resource.PlusTicketButton).click()
        if current_num > expect_num:
            for _ in range(current_num - expect_num):
                self.selector(resourceId=self.Resource.SubTicketButton).click()


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
            return None
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
        if checkboxs is None:
            raise StateError("无需选择用户")
        if len(checkboxs) != len(self.audiences):
            raise StateError("发现未知购票人，请先在应用中填好购票人信息")
        for checkbox in checkboxs:
            if checkbox.attrib["checked"] != "true":
                checkbox.click()

    def skip_popup(self):
        dialog = self.dialog
        if dialog.exists():
            dialog.child_selector(resourceId=self.Resource.DialogConfirmButton).click()

    def confirm(self):
        self.choose_audiences()
        pay_page = PayPage(self.device)
        ticket_bought = False
        while not ticket_bought:
            self.confirm_button.wait()
            self.confirm_button.click()
            if pay_page.wait(0.1):
                ticket_bought = True
            else:
                self.skip_popup()
        return pay_page


class PayPage(_Page):
    name = "支付"

    def wait(self, timeout: None | float = None) -> bool:
        res = self.selector(resourceId="com.alipay.android.app:id/flybird_layout").wait(exists=True, timeout=timeout)
        if res:
            print("抢票成功")
            return True
        return False


def run(audiences: list[str]):
    device = u2.connect()
    sell_page = SellPage(device=device)
    sell_page.wait()
    sell_page.wait_for_sell()
    select_page = sell_page.confirm()
    select_page.wait()
    select_page.set_perform(0)
    select_page.set_available_ticket(0)
    select_page.set_ticket_number(len(audiences))
    select_page.choose_perform()
    select_page.choose_ticket()
    select_page.choose_ticket_num()
    buy_page = select_page.confirm()
    # buy_page = BuyPage(device=device)
    buy_page.wait()
    buy_page.set_audiences(audiences)
    buy_page.confirm()


@click.command
@click.argument("audiences", nargs=-1, required=True)
def cli(audiences: tuple[str, ...]):
    run(audiences=list(audiences))


if __name__ == "__main__":
    run(audiences=["龙相丞"])
