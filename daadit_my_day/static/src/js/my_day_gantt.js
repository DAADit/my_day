/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { Component } from "@odoo/owl";

const KIND_LABEL = {
    ticket: "Helpdesk", task: _t("Task"), own: _t("Personal work"), activity: _t("Activity"), event: _t("Appointment"),
};
const STATUS_LABEL = {
    new: _t("Newly scheduled"),
    moved: _t("Moved"),
    moved_away: _t("Previous time"),
    removed: _t("Removed"),
    same: "",
};

/**
 * Een Gantt-weergave van één of meer dagen: per dag een balk met het rooster,
 * afspraken en werkblokken. In de herplanning toont hij ook wat verschuift.
 */
export class MyDayGantt extends Component {
    static template = "daadit_my_day.Gantt";
    static props = {
        rows: Array,
        selectedKey: { type: [String, Boolean], optional: true },
        selectedDate: { type: [String, Boolean], optional: true },
        onSelect: { type: Function, optional: true },
        showLegend: { type: Boolean, optional: true },
        compare: { type: Boolean, optional: true },
    };

    get range() {
        let from = 8 * 60;
        let to = 17 * 60;
        for (const row of this.props.rows) {
            for (const [start, end] of row.work) {
                from = Math.min(from, start);
                to = Math.max(to, end);
            }
            for (const item of row.items) {
                if (item.end > item.start) {
                    from = Math.min(from, item.start);
                    to = Math.max(to, item.end);
                }
            }
        }
        from = Math.max(0, Math.floor(from / 60) * 60);
        to = Math.min(1440, Math.ceil(to / 60) * 60);
        if (to - from < 120) {
            to = Math.min(1440, from + 120);
        }
        return { from, to };
    }

    get hours() {
        const { from, to } = this.range;
        const result = [];
        for (let minute = from; minute <= to; minute += 60) {
            result.push({ minute, label: `${String(minute / 60).padStart(2, "0")}:00` });
        }
        return result;
    }

    get gridStyle() {
        const { from, to } = this.range;
        return `--o-gantt-hour: ${(6000 / (to - from)).toFixed(4)}%;`;
    }

    left(minute) {
        const { from, to } = this.range;
        const clamped = Math.min(Math.max(minute, from), to);
        return ((clamped - from) / (to - from)) * 100;
    }

    span(start, end) {
        const left = this.left(start);
        const width = Math.max(this.left(end) - left, 0.6);
        return `left: ${left.toFixed(3)}%; width: ${width.toFixed(3)}%;`;
    }

    kindLabel(kind) {
        return KIND_LABEL[kind] || "";
    }

    statusLabel(status) {
        return STATUS_LABEL[status] || "";
    }

    barTitle(item) {
        const parts = [this.kindLabel(item.kind), item.time, item.title];
        if (item.from) {
            parts.push(_t("previously %s", item.from));
        }
        if (this.props.compare && this.statusLabel(item.status)) {
            parts.push(this.statusLabel(item.status));
        }
        return parts.filter(Boolean).join(" · ");
    }

    barClass(item) {
        return {
            o_gantt_bar: true,
            [`kind_${item.kind}`]: true,
            [`status_${item.status}`]: this.props.compare,
            ghost: ["moved_away", "removed"].includes(item.status),
            done: item.state === "done",
            focus: item.focus,
            running: item.running,
            selected: this.props.selectedKey === item.key,
            clickable: Boolean(this.props.onSelect && item.key && !item.key.startsWith("ghost-")),
        };
    }

    rowClass(row) {
        return {
            o_gantt_row: true,
            today: row.is_today,
            closed: row.closed,
            selected: this.props.selectedDate === row.date,
        };
    }

    countByStatus(status) {
        let total = 0;
        for (const row of this.props.rows) {
            total += row.items.filter((item) => item.status === status).length;
        }
        return total;
    }

    onBar(row, item) {
        if (this.props.onSelect && item.key && !item.key.startsWith("ghost-")) {
            this.props.onSelect(row, item);
        }
    }
}
