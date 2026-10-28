"""Strict bare-message routing for the daily draw command."""

import astrbot.api.message_components as Comp
from astrbot.api.event import AstrMessageEvent, filter


class DailyDrawTrigger(filter.CustomFilter):
    """Reject unrelated messages before AstrBot wakes or activates the handler."""

    def filter(self, event: AstrMessageEvent, cfg) -> bool:
        messages = event.get_messages()
        if (
            event.get_message_str().strip() != "今日宝可梦"
            or not messages
            or any(not isinstance(message, Comp.Plain) for message in messages)
            or "".join(message.text for message in messages).strip() != "今日宝可梦"
            or event.get_sender_id() == event.get_self_id()
        ):
            return False

        # Use the live command metadata so console changes apply immediately.
        # CommandFilter.filter itself requires a wake prefix; only its name and
        # custom restrictions are reused for the bare-message route.
        from astrbot.core.star.filter.command import CommandFilter
        from astrbot.core.star.star_handler import star_handlers_registry

        main_module = f"{__package__}.main" if __package__ else "main"
        metadata = star_handlers_registry.get_handler_by_full_name(
            f"{main_module}_cmd_partner_draw"
        )
        if metadata is None or not metadata.enabled:
            return False
        for command_filter in metadata.event_filters:
            if isinstance(command_filter, CommandFilter):
                if not command_filter.equals(
                    "今日宝可梦"
                ) or not command_filter.custom_filter_ok(event, cfg):
                    return False
            elif not command_filter.filter(event, cfg):
                return False
        return True
