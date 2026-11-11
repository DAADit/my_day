/** @odoo-module **/

import { Component, onWillStart, useRef, useState } from "@odoo/owl";
import { formatDateTime, deserializeDateTime } from "@web/core/l10n/dates";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useSortable } from "@web/core/utils/sortable_owl";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

const SOURCES = {
    todo: _t("To-do"), task: _t("Task"), ticket: "Helpdesk", activity: _t("Activity"),
};

class MyDayWorkspace extends Component {
    static template = "daadit_my_day_core.Workspace";
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            scope: "mine", items: [], employees: [], appointments: [], userId: null,
            dirty: false, busy: false,
        });
        const list = useRef("items");
        useSortable({
            ref: list,
            elements: ".o_my_day_core_sortable",
            handle: ".o_my_day_core_handle",
            onDrop: ({ element, previous }) => this.move(element.dataset.key, previous?.dataset.key),
        });
        onWillStart(() => this.load());
    }

    get scope() {
        const scope = this.state.scope;
        return scope === "mine" ? null : scope === "all" ? scope : Number(scope);
    }

    call(method, args) {
        return this.orm.call("daadit.my.day.sources", method, args);
    }

    async load() {
        this.state.busy = true;
        try {
            const result = await this.call("get_workspace", [this.scope]);
            Object.assign(this.state, {
                items: result.items, employees: result.employees, appointments: result.appointments,
                userId: result.user_id, dirty: false,
            });
        } finally {
            this.state.busy = false;
        }
    }

    async changeScope(event) {
        this.state.scope = event.target.value;
        await this.load();
    }

    move(key, previousKey) {
        if (this.state.busy || this.state.scope !== "mine") {
            return;
        }
        const items = this.state.items;
        const from = items.findIndex((item) => item.key === key && !item.urgent);
        if (from < 0) {
            return;
        }
        const [item] = items.splice(from, 1);
        const previous = items.findIndex((row) => row.key === previousKey);
        const urgentCount = items.filter((row) => row.urgent).length;
        items.splice(Math.max(urgentCount, previous + 1), 0, item);
        this.state.dirty = true;
    }

    async save() {
        this.state.busy = true;
        try {
            const keys = this.state.items.filter((item) => !item.urgent).map((item) => item.key);
            await this.call("save_order", [keys]);
            this.state.dirty = false;
        } finally {
            this.state.busy = false;
        }
    }

    async assign(item, event) {
        const target = Number(event.target.value);
        if (!target || this.state.busy || this.state.dirty) {
            return;
        }
        this.state.busy = true;
        try {
            await this.call("assign", [item.source_kind, item.id, target, this.scope]);
            await this.load();
        } finally {
            this.state.busy = false;
        }
    }

    open(model, id, view = false) {
        return this.action.doAction({
            type: "ir.actions.act_window", res_model: model, res_id: id,
            views: [[view, "form"]], target: "new",
        }, { onClose: () => this.state.dirty ? undefined : this.load() });
    }

    sourceLabel(kind) {
        return SOURCES[kind] || kind;
    }

    formatStart(value) {
        return formatDateTime(deserializeDateTime(value));
    }
}

registry.category("actions").add("daadit_my_day_core.workspace", MyDayWorkspace);
