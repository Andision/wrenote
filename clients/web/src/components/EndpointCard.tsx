// Settings → Models: point one slot at a model served over HTTP.
//
// The other half of the model choice. A slot is answered either by a file this
// machine downloaded (ModelPicker, above) or by something at a URL — and the
// switch here is what decides which, so the two live in the same section
// rather than in a separate "advanced" corner where the choice would read as
// two unrelated settings.
//
// The API key is write-only by design: the engine reports `has_api_key` and
// never the key, so the field shows "saved" and an omitted value means "leave
// it". See lib/models.EndpointPatch.
import { useState } from "react";
import { Check, Cloud, HardDrive, Loader2, ShieldAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { useT } from "@/i18n";
import type { EndpointSlot, EndpointStatus } from "@/lib/models";

type TestResult = { ok: boolean; reply?: string; error?: string };

export function EndpointCard({
  slot,
  status,
  busy,
  open,
  onOpenChange,
  onSave,
  onTest,
}: {
  slot: EndpointSlot;
  status: EndpointStatus;
  busy?: boolean;
  /** Owned by the panel, not by this component: the fields below are reset by
   *  remounting on a new `key` when the engine's answer changes (see
   *  ModelsPanel), and a saved form that collapsed itself in the process
   *  would be a strange thing to watch happen. */
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Resolves once the engine has stored it and the panel has refreshed. */
  onSave: (patch: {
    base_url?: string;
    model?: string;
    api_key?: string;
    api_key_env?: string;
    active?: boolean;
  }) => Promise<void>;
  onTest: () => Promise<TestResult>;
}) {
  const t = useT();
  const [baseUrl, setBaseUrl] = useState(status.base_url);
  const [model, setModel] = useState(status.model);
  const [apiKey, setApiKey] = useState("");
  const [apiKeyEnv, setApiKeyEnv] = useState(status.api_key_env);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<TestResult | null>(null);

  const dirty =
    baseUrl.trim() !== status.base_url ||
    model !== status.model ||
    apiKeyEnv !== status.api_key_env ||
    apiKey !== "";

  // The engine is the source of truth, and these fields follow what it stored
  // rather than what was typed (base_url is trimmed server-side, for one).
  // That happens by remounting on a fresh key — writing them back from an
  // effect is the shape that goes stale, and the lint rule that catches it.
  const save = async (extra: { active?: boolean } = {}) => {
    setResult(null);
    await onSave({
      base_url: baseUrl.trim(),
      model,
      api_key_env: apiKeyEnv,
      // Omitted unless typed: sending "" would clear a key the form cannot
      // see, which is not what "I only changed the model name" means.
      ...(apiKey === "" ? {} : { api_key: apiKey }),
      ...extra,
    });
  };

  const test = async () => {
    setTesting(true);
    setResult(null);
    try {
      setResult(await onTest());
    } catch (e) {
      setResult({ ok: false, error: e instanceof Error ? e.message : String(e) });
    } finally {
      setTesting(false);
    }
  };

  const toggle = (on: boolean) => {
    // Switching on with nothing to switch to would leave the slot unable to
    // answer; open the form instead of sending a request that must fail.
    if (on && !baseUrl.trim()) {
      onOpenChange(true);
      return;
    }
    void (on ? save({ active: true }) : onSave({ active: false }));
  };

  return (
    <div
      className={`rounded-lg border ${
        status.active ? "border-brand-500 bg-brand-500/10" : "border-border/50 bg-background/40"
      }`}
    >
      <div className="flex items-center gap-2 px-3 py-2.5">
        {status.active ? (
          <Cloud className="size-3.5 shrink-0 text-brand-500" />
        ) : (
          <HardDrive className="size-3.5 shrink-0 text-muted-foreground" />
        )}
        <button
          type="button"
          onClick={() => onOpenChange(!open)}
          className="min-w-0 flex-1 text-left"
        >
          <span className="text-[13px] font-medium">{t("models.endpoint.title")}</span>
          <span className="block truncate text-[11px] text-muted-foreground">
            {status.configured ? status.base_url : t("models.endpoint.none")}
          </span>
        </button>
        <Switch
          checked={status.active}
          disabled={busy}
          onCheckedChange={toggle}
          aria-label={t("models.endpoint.use")}
        />
      </div>

      {open && (
        <div className="space-y-2.5 border-t border-border/50 px-3 py-3">
          <Field
            label={t("models.endpoint.baseUrl")}
            hint={t("models.endpoint.baseUrlHint")}
            value={baseUrl}
            onChange={setBaseUrl}
            placeholder="http://127.0.0.1:8080/v1"
            disabled={busy}
          />
          <Field
            label={t("models.endpoint.model")}
            hint={t("models.endpoint.modelHint")}
            value={model}
            onChange={setModel}
            placeholder={t("models.endpoint.modelPlaceholder")}
            disabled={busy}
          />
          <Field
            label={t("models.endpoint.apiKey")}
            hint={
              status.has_api_key ? t("models.endpoint.apiKeySaved") : t("models.endpoint.apiKeyHint")
            }
            value={apiKey}
            onChange={setApiKey}
            type="password"
            placeholder={status.has_api_key ? "••••••••" : ""}
            disabled={busy}
            action={
              status.has_api_key
                ? {
                    label: t("models.endpoint.apiKeyClear"),
                    onClick: () => void onSave({ api_key: "" }),
                  }
                : undefined
            }
          />
          <Field
            label={t("models.endpoint.apiKeyEnv")}
            hint={t("models.endpoint.apiKeyEnvHint")}
            value={apiKeyEnv}
            onChange={setApiKeyEnv}
            placeholder="OPENAI_API_KEY"
            disabled={busy}
          />

          {/* The privacy consequence, stated where the decision is made and
              not only on the recording screen. Loopback is exempt: that is a
              model server on this machine. */}
          {status.configured && !status.local && (
            <p className="flex items-start gap-1.5 rounded-md bg-amber-50 px-2 py-1.5 text-[11px] text-amber-700 dark:bg-amber-950/30 dark:text-amber-300">
              <ShieldAlert className="mt-px size-3.5 shrink-0" />
              <span>{t("models.endpoint.remoteWarning")}</span>
            </p>
          )}

          <div className="flex flex-wrap items-center gap-2 pt-0.5">
            <Button
              size="sm"
              onClick={() => void save()}
              disabled={busy || !dirty || !baseUrl.trim()}
            >
              {t("models.endpoint.save")}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => void test()}
              // Tests what is stored, so an unsaved edit would test the old
              // value and report a pass about something else. Save first.
              disabled={busy || testing || dirty || !status.configured}
            >
              {testing && <Loader2 className="size-3.5 animate-spin" />}
              {t("models.endpoint.test")}
            </Button>
            {result && (
              <span
                className={`flex min-w-0 items-center gap-1 text-[11px] ${
                  result.ok ? "text-emerald-600 dark:text-emerald-400" : "text-destructive"
                }`}
              >
                {result.ok && <Check className="size-3.5 shrink-0" />}
                <span className="truncate">
                  {result.ok
                    ? t("models.endpoint.testOk", { reply: result.reply ?? "" })
                    : result.error}
                </span>
              </span>
            )}
          </div>
          <p className="text-[11px] text-muted-foreground">
            {t(`models.endpoint.applies.${slot}`)}
          </p>
        </div>
      )}
    </div>
  );
}

function Field({
  label,
  hint,
  value,
  onChange,
  placeholder,
  type,
  disabled,
  action,
}: {
  label: string;
  hint: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
  disabled?: boolean;
  action?: { label: string; onClick: () => void };
}) {
  return (
    <label className="block space-y-1">
      <span className="flex items-baseline gap-2 text-[11px] font-medium">
        {label}
        <span className="min-w-0 flex-1 truncate font-normal text-muted-foreground">{hint}</span>
        {action && (
          <button
            type="button"
            onClick={action.onClick}
            disabled={disabled}
            className="shrink-0 font-normal text-muted-foreground underline-offset-2 hover:text-destructive hover:underline disabled:opacity-50"
          >
            {action.label}
          </button>
        )}
      </span>
      <Input
        value={value}
        type={type}
        placeholder={placeholder}
        disabled={disabled}
        spellCheck={false}
        autoComplete="off"
        onChange={(e) => onChange(e.target.value)}
        className="text-[13px]"
      />
    </label>
  );
}
