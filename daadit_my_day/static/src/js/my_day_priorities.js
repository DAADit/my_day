/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { Component, onWillStart, useRef, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { useSortable } from "@web/core/utils/sortable_owl";

const KIND_LABEL = { ticket: "Helpdesk", task: _t("Task"), own: _t("Personal work"), activity: _t("Activity") };

function formatMinutes(total) {
    const minutes = Math.round(total || 0);
    if (minutes < 60) {
        return _t("%s min", minutes);
    }
    const rest = minutes % 60;
    return rest ? _t("%sh %sm", Math.floor(minutes / 60), String(rest).padStart(2, "0")) : _t("%sh", minutes / 60);
}

/** Al het open werk in één lijst die je zelf in volgorde sleept. */
export class MyDayPriorities extends Component {
    static template = "daadit_my_day.Priorities";
    static props = { onClose: Function, onSaved: Function };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.listRef = useRef("list");
        this.state = useState({
            items: [], fixed: [], employees: [], scope: "mine",
            hasCustom: false, dirty: false, busy: false, filter: "all",
        });
        useSortable({
            ref: this.listRef,
            elements: ".o_my_day_prio_item.is-sortable",
            handle: ".o_my_day_prio_handle",
            cursor: "grabbing",
            onDrop: ({ element, previous }) => {
                const rows = this.visibleItems.filter((item) => item.can_reorder);
                const from = rows.findIndex((item) => item.key === element.dataset.key);
                const previousIndex = rows.findIndex((item) => item.key === previous?.dataset.key);
                const to = previousIndex + (previousIndex < from ? 1 : 0);
                this.reorder(rows, from, to);
            },
        });
        onWillStart(() => this.load());
    }

    kindLabel(kind) {
        return KIND_LABEL[kind] || "";
    }

    formatMinutes(minutes) {
        return formatMinutes(minutes);
    }

    get visibleItems() {
        const filter = this.state.filter;
        return filter === "all" ? this.state.items : this.state.items.filter((item) => item.kind === filter);
    }

    get counts() {
        const counts = { all: this.state.items.length, ticket: 0, task: 0, activity: 0 };
        for (const item of this.state.items) {
            counts[item.kind] = (counts[item.kind] || 0) + 1;
        }
        return counts;
    }

    async load() {
        this.state.busy = true;
        try {
            const result = await this.orm.call("daadit.my.day", "get_priorities", [this.scope]);
            this.state.items = result.items;
            this.state.fixed = result.fixed;
            this.state.employees = result.employees;
            this.state.hasCustom = result.has_custom;
            this.state.dirty = false;
        } finally {
            this.state.busy = false;
        }
    }

    get scope() {
        return this.state.scope === "mine" ? null : this.state.scope === "all"
            ? "all" : Number(this.state.scope);
    }

    reorder(rows, from, to) {
        if (this.state.busy || from < 0 || to < 0 || to >= rows.length || to === from) {
            return;
        }
        const keys = new Set(rows.map((item) => item.key));
        const [moved] = rows.splice(from, 1);
        rows.splice(to, 0, moved);
        let index = 0;
        this.state.items = this.state.items.map((item) => keys.has(item.key) ? rows[index++] : item);
        this.state.dirty = true;
    }

    step(item, delta) {
        const rows = this.visibleItems.filter((row) => row.can_reorder);
        const from = rows.indexOf(item);
        this.reorder(rows, from, from + delta);
    }

    toTop(item) {
        const rows = this.visibleItems.filter((row) => row.can_reorder);
        this.reorder(rows, rows.indexOf(item), 0);
    }

    setFilter(filter) {
        this.state.filter = filter;
    }

    async changeScope(event) {
        this.state.scope = event.target.value;
        await this.load();
    }

    async openItem(item) {
        await this.action.doAction({
            type: "ir.actions.act_window",
            res_model: item.open_model,
            res_id: item.open_id,
            views: [[item.open_view_id || false, "form"]],
            target: "new",
        }, { onClose: () => this.state.dirty ? undefined : this.load() });
    }

    async assign(item, event) {
        const target = Number(event.target.value);
        if (!target || this.state.busy || this.state.dirty) {
            return;
        }
        this.state.busy = true;
        try {
            await this.orm.call("daadit.my.day", "assign_priority", [
                item.res_model, item.res_id, target, this.scope,
            ]);
            await this.load();
            this.notification.add(_t("Assignment saved."), { type: "success" });
        } finally {
            this.state.busy = false;
        }
    }

    async save() {
        if (this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            const refs = this.state.items.filter((item) => item.can_reorder)
                .map((item) => [item.res_model, item.res_id]);
            const mine = this.scope === null;
            await this.orm.call("daadit.my.day", mine ? "set_priorities" : "set_employee_priorities",
                mine ? [refs] : [this.scope, refs]);
            this.state.dirty = false;
            this.notification.add(_t("The work order was saved."), { type: "success" });
            if (mine) {
                this.props.onSaved();
            } else {
                await this.load();
            }
        } finally {
            this.state.busy = false;
        }
    }

    async reset() {
        if (this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            await this.orm.call("daadit.my.day", "reset_priorities", []);
        } finally {
            this.state.busy = false;
        }
        await this.load();
        this.notification.add(_t("The default order has been restored."), { type: "info" });
    }
}
