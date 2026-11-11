/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { Component, onWillStart, onWillUnmount, useChildSubEnv, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { CallbackRecorder, useSetupAction } from "@web/search/action_hook";
import { getDefaultConfig, View } from "@web/views/view";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { MyDayGantt } from "./my_day_gantt";
import { MyDayPriorities } from "./my_day_priorities";

const URGENT_POLL_MS = 120000;
const HEARTBEAT_MS = 60000;
const LONG_SESSION_SECONDS = 7200;
const KIND_LABEL = { ticket: "Helpdesk", task: _t("Task"), own: _t("Personal work"), activity: _t("Activity"), event: _t("Appointment") };
const LABELS = {
    addEstimate: _t("Add estimate"),
    blockWork: _t("Block work"),
    blocked: _t("Blocked"),
    closeAndNext: _t("Close and next"),
    closeTheTicketOrTaskAndContinue: _t("Close the ticket or task and continue"),
    completeActivity: _t("Complete activity"),
    completeThisActivity: _t("Complete this activity"),
    completed: _t("Completed"),
    dayReview: _t("Day review"),
    done: _t("Done"),
    editFollowUp: _t("Edit follow-up"),
    enableTimesheets: _t("Enable timesheets"),
    finishBlock: _t("Finish block"),
    noHelpdesk: _t("No helpdesk"),
    noTimesheet: _t("No timesheet"),
    notScheduledToWork: _t("Not scheduled to work"),
    off: _t("off"),
    pause: _t("Pause"),
    startTimer: _t("Start timer"),
    timeEstimate: _t("Time estimate"),
    waitingForCustomer: _t("Waiting for customer"),
};
const LAYOUT_KEY = "daadit_my_day.layout";
const LAYOUTS = ["side", "top", "hidden"];

function readLayout() {
    try {
        const value = browser.localStorage.getItem(LAYOUT_KEY);
        return LAYOUTS.includes(value) ? value : "side";
    } catch {
        return "side";
    }
}

function formatMinutes(total) {
    const minutes = Math.round(total || 0);
    if (minutes < 60) {
        return _t("%s min", minutes);
    }
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    return rest ? _t("%sh %sm", hours, String(rest).padStart(2, "0")) : _t("%sh", hours);
}

/** Het gewone Odoo-formulier, los van de broodkruimels van de app. */
class MyDayRecord extends Component {
    static template = "daadit_my_day.Record";
    static components = { View };
    static props = {
        resModel: String,
        resId: Number,
        recorder: Object,
    };

    setup() {
        useChildSubEnv({ config: getDefaultConfig() });
    }

    get viewProps() {
        return {
            type: "form",
            resModel: this.props.resModel,
            resId: this.props.resId,
            context: {},
            display: { controlPanel: false },
            preventCreate: true,
            __beforeLeave__: this.props.recorder,
        };
    }
}

class MyDayReview extends Component {
    labels = LABELS;

    static template = "daadit_my_day.Review";
    static props = { mode: String, date: String, onClose: Function, onChanged: Function };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        const start = new Date(`${this.props.date}T00:00:00Z`);
        if (this.props.mode === "done") {
            start.setUTCDate(start.getUTCDate() - 6);
        }
        this.state = useState({
            from: start.toISOString().slice(0, 10),
            to: this.props.date,
            employee: false,
            result: null,
            correction: null,
            busy: false,
        });
        onWillStart(() => this.load());
    }

    duration(minutes) {
        return formatMinutes(minutes);
    }

    async load() {
        this.state.busy = true;
        try {
            this.state.result = await this.orm.call("daadit.my.day", "get_day_review", [
                this.state.from, this.state.to, Number(this.state.employee) || false,
                this.props.mode === "done",
            ]);
            this.state.employee = this.state.result.employee_id;
        } finally {
            this.state.busy = false;
        }
    }

    async resolve(row, operation) {
        if (this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            this.state.result = await this.orm.call("daadit.my.day", "review_day_block", [
                row.id, operation, this.props.date,
            ]);
            this.props.onChanged();
        } finally {
            this.state.busy = false;
        }
    }

    openCorrection(row) {
        this.state.correction = {
            row,
            minutes: Math.round(row.seconds / 60),
            reason: "",
        };
    }

    cancelCorrection() {
        this.state.correction = null;
    }

    async saveCorrection() {
        const correction = this.state.correction;
        if (!correction || this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            await this.orm.call("daadit.my.day", "correct_day_time", [
                correction.row.id, Number(correction.minutes), correction.reason,
            ]);
            this.state.correction = null;
            this.props.onChanged();
            await this.load();
        } finally {
            this.state.busy = false;
        }
    }

    async openRecord(model, id) {
        await this.action.doAction({
            type: "ir.actions.act_window",
            res_model: model,
            res_id: id,
            views: [[false, "form"]],
            target: "new",
        }, { onClose: () => this.load() });
    }
}

export class MyDay extends Component {
    labels = LABELS;

    static template = "daadit_my_day.Main";
    static components = { MyDayRecord, MyDayReview, MyDayGantt, MyDayPriorities };
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.dialog = useService("dialog");
        this.recorder = new CallbackRecorder();
        this.env.config.setDisplayName(_t("My Day"));
        this.state = useState({
            loading: true,
            busy: false,
            error: false,
            day: null,
            selected: null,
            showAdd: false,
            showQueue: false,
            showBlocked: false,
            reviewMode: null,
            reviewChanged: false,
            blockingForm: null,
            estimateForm: null,
            sharedForm: null,
            focusForm: null,
            replanPreview: null,
            showShared: false,
            detailOpen: false,
            ownTitle: "",
            ownMinutes: 30,
            ownOutsideHours: false,
            urgentCount: 0,
            tick: Date.now(),
            activeTimer: null,
            layout: readLayout(),
            gantt: null,
            view: null,
            showChanges: false,
        });
        const view = this.props.action?.params?.view;
        if (view === "done" || view === "day") {
            this.state.reviewMode = view;
        } else if (view === "priorities") {
            this.state.view = "priorities";
        }
        useSetupAction({ beforeLeave: () => this.saveRecord() });
        this.timerLoadedAt = Date.now();
        onWillStart(() => this.call("get_day", []));
        this.pollTimer = setInterval(() => this.pollUrgent(), URGENT_POLL_MS);
        this.tickTimer = setInterval(() => (this.state.tick = Date.now()), 1000);
        this.beatTimer = setInterval(() => this.heartbeat(), HEARTBEAT_MS);
        onWillUnmount(() => {
            clearInterval(this.pollTimer);
            clearInterval(this.tickTimer);
            clearInterval(this.beatTimer);
        });
    }

    formatMinutes(minutes) {
        return formatMinutes(minutes);
    }

    kindLabel(kind) {
        return KIND_LABEL[kind] || "";
    }

    get day() {
        return this.state.day;
    }

    get allItems() {
        return [
            ...(this.day?.blocks || []), ...(this.day?.queue || []),
            ...(this.day?.blocked_work || []), ...(this.day?.shared_elsewhere || []),
        ];
    }

    get selected() {
        return this.state.selected;
    }

    get selectedIsBlock() {
        return Boolean(this.selected?.block_id);
    }

    get canAct() {
        return (
            this.selectedIsBlock && this.selected.state === "planned" && !this.day?.is_past
        );
    }

    freeTimeLabel(minutes) {
        return _t("%s free", formatMinutes(minutes));
    }

    get headerText() {
        const stats = this.day.stats;
        const parts = [
            _t("%s available", formatMinutes(stats.available)),
            _t("%s scheduled", formatMinutes(stats.planned)),
            _t("%s free", formatMinutes(stats.free)),
            _t("%s to do", stats.open),
        ];
        if (stats.done) {
            parts.push(_t("%s done", stats.done));
        }
        return parts.join(" · ");
    }

    get timerRunning() {
        return Boolean(this.selected?.timer_running);
    }

    get timerText() {
        const selected = this.selected;
        let seconds = selected?.timer_seconds || 0;
        if (selected?.timer_running) {
            seconds += Math.max(0, Math.floor((this.state.tick - this.timerLoadedAt) / 1000));
        }
        const pad = (value) => String(value).padStart(2, "0");
        return `${pad(Math.floor(seconds / 3600))}:${pad(Math.floor(seconds / 60) % 60)}:${pad(
            seconds % 60
        )}`;
    }

    weekClass(day) {
        return {
            o_my_day_chip: true,
            active: day.selected,
            today: day.is_today,
            off: day.off,
            closed: day.closed,
        };
    }

    blockClass(block) {
        return {
            o_my_day_block: true,
            [`kind_${block.kind}`]: true,
            done: block.state === "done",
            running: block.running && block.state !== "done",
            overflow: block.overflow,
            selected: this.selected?.key === block.key,
            timing: block.timer_running,
        };
    }

    /** Sla een lopende wijziging in het formulier op voordat we wisselen. */
    async saveRecord() {
        const results = await Promise.all(this.recorder.callbacks.map((cb) => cb()));
        return !results.includes(false);
    }

    apply(payload, preferKey) {
        this.state.day = payload;
        if (this.state.layout === "top") {
            this.loadGantt();
        }
        if (payload.blocked_work.some((item) => item.followup_due)) {
            this.state.showBlocked = true;
        }
        const items = this.allItems;
        const wanted = payload.select || preferKey;
        let selected = items.find((item) => item.key === wanted);
        if (!selected) {
            selected = items.find((item) => item.key === payload.next_key) || null;
        }
        this.state.selected = selected;
        if (selected?.blocked) {
            this.state.showBlocked = true;
        }
        if (payload.message) {
            this.notification.add(payload.message, { type: "success" });
        }
        this.state.urgentCount = payload.urgent.length;
        const running = payload.blocks.find((item) => item.key === payload.running_key);
        if (running) {
            this.setActiveTimer(running, running.timer_session_seconds);
        } else if (payload.is_today) {
            this.state.activeTimer = null;
        }
        this.timerLoadedAt = Date.now();
    }

    setActiveTimer(block, sessionSeconds) {
        this.state.activeTimer = {
            id: block.block_id,
            title: block.title,
            sessionSeconds,
            loadedAt: Date.now(),
        };
    }

    get longTimer() {
        const active = this.state.activeTimer;
        return active && active.sessionSeconds +
            Math.max(0, (this.state.tick - active.loadedAt) / 1000) >= LONG_SESSION_SECONDS;
    }

    markTimer(info, block) {
        for (const item of this.day.blocks) {
            const matches = item.key === info.key;
            item.timer_running = matches && info.running;
            if (matches) {
                item.timer_seconds = info.seconds;
                item.timer_session_seconds = info.session_seconds;
            }
        }
        this.day.running_key = info.running ? info.key : false;
        if (info.running) {
            this.setActiveTimer(block, info.session_seconds);
        } else {
            this.state.activeTimer = null;
        }
        this.timerLoadedAt = Date.now();
        this.state.tick = Date.now();
    }

    async heartbeat() {
        const active = this.state.activeTimer;
        if (!active || this.state.busy) {
            return;
        }
        try {
            const alive = await this.orm.silent.call("daadit.my.day", "heartbeat", [active.id]);
            if (!alive && this.state.activeTimer === active && !this.state.busy) {
                this.state.activeTimer = null;
                await this.call("get_day", [this.day.date], this.selected?.key);
                this.notification.add(
                    _t("The timer stopped. Check your time and resume when you continue working."),
                    { type: "warning" }
                );
            }
        } catch {
            // After a connection failure, the next heartbeat reconciles the display.
        }
    }

    async changeTimer(method, block) {
        if (!block || this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            const info = await this.orm.call("daadit.my.day", method, [block.block_id]);
            this.markTimer(info, block);
        } finally {
            this.state.busy = false;
        }
    }

    togglePause() {
        return this.changeTimer(this.timerRunning ? "pause_block" : "open_block", this.selected);
    }

    pauseActive() {
        return this.changeTimer("pause_block", { block_id: this.state.activeTimer?.id });
    }

    toggleNoHours() {
        return this.act("toggle_no_hours", [this.selected.block_id], this.selected.key);
    }

    async call(method, args, preferKey) {
        this.state.busy = true;
        try {
            const payload = await this.orm.call("daadit.my.day", method, args);
            this.apply(payload, preferKey);
            this.state.error = false;
        } catch (error) {
            if (this.state.loading) {
                this.state.error = error.data?.message || error.message || _t("Something went wrong.");
            } else {
                throw error;
            }
        } finally {
            this.state.busy = false;
            this.state.loading = false;
        }
    }

    async act(method, args, preferKey) {
        if (this.state.busy || !(await this.saveRecord())) {
            return;
        }
        await this.call(method, args, preferKey);
    }

    async pollUrgent() {
        if (document.hidden || !this.day?.is_today || this.state.busy) {
            return;
        }
        const queue = await this.orm.call("daadit.my.day", "get_urgent", []);
        this.state.urgentCount = queue.length;
        if (queue.length) {
            this.state.day.urgent = queue;
        }
    }

    async select(item) {
        if (this.state.selected?.key === item.key) {
            this.state.detailOpen = true;
            return;
        }
        if (!(await this.saveRecord())) {
            return;
        }
        this.state.selected = item;
        this.state.focusForm = null;
        this.state.sharedForm = null;
        this.state.blockingForm = null;
        this.state.estimateForm = null;
        this.state.detailOpen = true;
    }

    closeDetail() {
        this.state.detailOpen = false;
    }

    openDay(date) {
        this.state.replanPreview = null;
        return this.act("get_day", [date]);
    }

    async openWorkingHours() {
        if (this.state.busy || !(await this.saveRecord())) {
            return;
        }
        const action = await this.orm.call("daadit.my.day", "get_working_hours_action", []);
        await this.action.doAction(action, { onClose: () => this.openDay(this.day.date) });
    }

    shiftDay(delta) {
        const [year, month, dayNumber] = this.day.date.split("-").map(Number);
        const target = new Date(Date.UTC(year, month - 1, dayNumber + delta));
        return this.openDay(target.toISOString().slice(0, 10));
    }

    goToday() {
        this.state.replanPreview = null;
        return this.act("get_day", []);
    }

    async replan() {
        if (this.state.busy || !(await this.saveRecord())) {
            return;
        }
        this.state.busy = true;
        try {
            this.state.replanPreview = await this.orm.call("daadit.my.day", "preview_replan", [this.day.date]);
        } finally {
            this.state.busy = false;
        }
    }

    cancelReplan() {
        this.state.replanPreview = null;
        this.state.showChanges = false;
    }

    async confirmReplan() {
        const { token } = this.state.replanPreview;
        this.cancelReplan();
        await this.act("confirm_replan", [this.day.date, token], this.selected?.key);
    }

    undoReplan() {
        this.cancelReplan();
        return this.act("undo_replan", [this.day.date]);
    }

    openFocusForm() {
        this.state.sharedForm = null;
        this.state.estimateForm = null;
        this.state.blockingForm = null;
        const { goal, focus, focus_interruptible: interruptible } = this.selected;
        this.state.focusForm = { goal, focus, interruptible };
    }

    cancelFocusForm() {
        this.state.focusForm = null;
    }

    async saveFocusForm() {
        const { goal, focus, interruptible } = this.state.focusForm;
        await this.act("set_day_focus", [
            this.selected.block_id, goal, focus, interruptible, this.day.date,
        ], this.selected.key);
        this.cancelFocusForm();
    }

    complete() {
        this.state.detailOpen = true;
        return this.act("complete_block", [this.selected.block_id]);
    }

    closeItem() {
        this.state.detailOpen = true;
        return this.act("close_block", [this.selected.block_id]);
    }

    later() {
        return this.act("later_today", [this.selected.block_id]);
    }

    defer() {
        return this.act("defer_block", [this.selected.block_id]);
    }

    planOutsideHours() {
        const { block_id: blockId } = this.selected;
        this.dialog.add(ConfirmationDialog, {
            body: _t("This block will be scheduled outside your working hours on this day and will stay there when you reschedule."),
            confirmLabel: _t("Schedule outside working hours"),
            confirm: () => this.act("plan_outside_hours", [blockId]),
        });
    }

    remove() {
        return this.act("remove_block", [this.selected.block_id]);
    }

    adjust(delta) {
        const minutes = Math.max(5, this.selected.minutes + delta);
        return this.act("set_block_minutes", [this.selected.block_id, minutes]);
    }

    pull(item) {
        return this.act("pull_item", [item.kind, item.activity_id || item.res_id, this.day.date]);
    }

    pullUrgent() {
        const item = this.day.urgent[0];
        return item && this.pull(item);
    }

    toggleAdd() {
        this.state.showAdd = !this.state.showAdd;
    }

    async openReview(mode) {
        if (this.state.busy || !(await this.saveRecord())) {
            return;
        }
        this.state.reviewMode = mode;
        this.state.reviewChanged = false;
    }

    async closeReview() {
        if (this.state.reviewChanged) {
            // Keep reviewMode active while refreshing so no new timer is started.
            await this.call("get_day", [this.day.date], this.selected?.key);
        }
        this.state.reviewMode = null;
    }

    reviewChanged() {
        this.state.reviewChanged = true;
    }

    toggleShared() {
        this.state.showShared = !this.state.showShared;
    }

    async openSharedForm() {
        const item = this.selected;
        const details = await this.orm.call("daadit.my.day", "shared_work_details", [
            item.res_id, item.block_id || false,
        ]);
        if (this.selected?.key === item.key) {
            this.state.blockingForm = null;
            this.state.estimateForm = null;
            this.state.focusForm = null;
            this.state.sharedForm = details;
        }
    }

    cancelSharedForm() {
        this.state.sharedForm = null;
    }

    async saveSharedForm() {
        const item = this.selected;
        const form = this.state.sharedForm;
        await this.act("set_shared_work", [
            item.res_id, Number(form.owner), form.parallel, item.block_id || false,
            form.part, this.day.date,
        ], item.key);
        this.state.sharedForm = null;
    }

    async openEstimateForm() {
        const item = this.selected;
        const details = await this.orm.call("daadit.my.day", "estimate_details", [item.kind, item.res_id]);
        if (this.selected?.key === item.key) {
            this.state.blockingForm = null;
            this.state.sharedForm = null;
            this.state.focusForm = null;
            this.state.estimateForm = {
                known: details.known,
                remaining: details.minutes,
                maxBlock: details.max_block,
                suggestion: details.suggestion,
            };
        }
    }

    applyEstimateSuggestion() {
        this.state.estimateForm.remaining = this.state.estimateForm.suggestion.remaining;
    }

    cancelEstimateForm() {
        this.state.estimateForm = null;
    }

    async saveEstimateForm() {
        const item = this.selected;
        const form = this.state.estimateForm;
        await this.act("set_work_estimate", [
            item.kind, item.res_id, Number(form.remaining), Number(form.maxBlock), this.day.date,
        ], item.key);
        this.state.estimateForm = null;
    }

    toggleBlocked() {
        this.state.showBlocked = !this.state.showBlocked;
    }

    async openBlockingForm() {
        const item = this.selected;
        const details = await this.orm.call("daadit.my.day", "blocking_details", [
            item.kind, item.res_id,
        ]);
        if (this.selected?.key === item.key) {
            this.state.estimateForm = null;
            this.state.sharedForm = null;
            this.state.focusForm = null;
            this.state.blockingForm = {
                kind: details.blocked || "blocked",
                reason: details.reason || "",
                followup: details.followup_date || details.default_followup,
                userId: details.followup_user_id || details.user_id,
                users: details.assignees,
                today: details.today,
            };
        }
    }

    cancelBlockingForm() {
        this.state.blockingForm = null;
    }

    async saveBlockingForm() {
        const item = this.selected;
        const form = this.state.blockingForm;
        await this.act("set_work_blocked", [
            item.kind, item.res_id, form.kind, form.reason, form.followup,
            Number(form.userId), this.day.date,
        ]);
        this.state.blockingForm = null;
    }

    async unblock() {
        const item = this.selected;
        await this.act("unblock_work", [item.kind, item.res_id, this.day.date]);
        this.state.blockingForm = null;
    }

    setLayout(layout) {
        this.state.layout = layout;
        try {
            browser.localStorage.setItem(LAYOUT_KEY, layout);
        } catch {
            // Without browser storage the choice lasts until the page reloads.
        }
        if (layout === "top") {
            this.loadGantt();
        }
    }

    async loadGantt() {
        if (!this.day) {
            return;
        }
        const date = this.day.date;
        const result = await this.orm.silent.call("daadit.my.day", "get_gantt", [date]);
        if (this.day?.date === date) {
            this.state.gantt = result;
        }
    }

    async selectFromGantt(row, item) {
        if (row.date !== this.day.date) {
            await this.openDay(row.date);
        }
        const target = this.allItems.find((candidate) => candidate.key === item.key);
        if (target) {
            await this.select(target);
        }
    }

    async completeActivity(activity) {
        await this.act("complete_activity", [activity.id, this.day.date], this.selected?.key);
    }

    openPriorities() {
        this.state.reviewMode = null;
        this.state.view = "priorities";
    }

    async closePriorities() {
        this.state.view = null;
        await this.call("get_day", [this.day.date], this.selected?.key);
    }

    async prioritiesSaved() {
        this.state.view = null;
        await this.call("get_day", [this.day.date], this.selected?.key);
        await this.replan();
    }

    toggleChanges() {
        this.state.showChanges = !this.state.showChanges;
    }

    toggleQueue() {
        this.state.showQueue = !this.state.showQueue;
    }

    async addOwn() {
        const title = this.state.ownTitle.trim();
        if (!title) {
            return;
        }
        await this.act("add_own_work", [
            title, Number(this.state.ownMinutes), this.day.date, this.state.ownOutsideHours,
        ]);
        this.state.ownTitle = "";
        this.state.ownOutsideHours = false;
        this.state.showAdd = false;
    }

    onOwnKeydown(ev) {
        if (ev.key === "Enter") {
            this.addOwn();
        }
    }
}

registry.category("actions").add("daadit_my_day.main", MyDay);
