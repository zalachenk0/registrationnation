// ============================================================================
// File:         App_BV_CG.tsx
// Written by:   BV (the form, steps and screens); CG (payment and Monday.com
//               calls, the Nov 19 fixes from the sponsor meeting, and the
//               front photo, security photo and sign-out pieces the Nov 17
//               rebuild dropped, tagged [CG]); SL (scan prompts)
// What it does: The whole touchscreen form: steps, scan prompts, review,
//               payment, printing and the final screen.
// Team-written: Logic written by BV (the form, steps and screens); CG (lines
//               tagged [CG]); SL (scan prompts). Outside code is used only
//               through the third-party sources listed below.
// Third-party:  React; Tailwind CSS styling; the page layout started from a
//               Figma design export (see the figma markers in index.html).
// ============================================================================
import { useState, useRef, useEffect, useId, createContext, useContext } from "react";
import logoUrl from "./imports/RN_Logo.png";
import { fillBlank, requestScan, submitPrint, printStatus, kioskFetch, saveLicenseFront, requestSecurityPhoto, staffSession, signOut, type PrintStatus, type ScanResult, type StaffUser } from "./integration_BV";
import { PAYMENT_AMOUNT, type CardEntry, type PaymentResult } from "./payment_CG"; // [CG]
import { TerminalPayment } from "./TerminalPayment_CG";
import { createMondayRecord } from "./monday_CG"; // [CG]
import { CompletionTimer } from "./CompletionTimer_BV";
import { displayDate, normalizeDate, renewalError, stepError, firstBlank, type FormStep } from "./renewal_BV";
import { transactionId } from "./transaction_CG"; // [CG]
import { counties, providers, vehicleModels, makes } from "./renewalOptions_BV";

const STATE_CODES = "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split(" ");
const stateName = (value = "") => value.toUpperCase() === "DC" ? "District of Columbia" : US_STATES[STATE_CODES.indexOf(value.toUpperCase())] || value;

type Step = 1 | 2 | 3 | 4 | 5;

interface PersonalInfo {
  firstName: string;
  lastName: string;
  dateOfBirth: string;
  email: string;
  phone: string;
  address: string;
  city: string;
  state: string;
  zip: string;
  county: string;
}

interface DriversLicense {
  licenseNumber: string;
  licenseState: string;
  expirationDate: string;
  licenseClass: string;
  vehicleYear: string;
  vehicleMake: string;
  vehicleModel: string;
  vin: string;
  licensePlate: string;
}

interface InsuranceInfo {
  provider: string;
  policyNumber: string;
  policyHolder: string;
  startDate: string;
  endDate: string;
  coverageType: string;
  agentName: string;
  agentPhone: string;
}


const US_STATES = [
  "Alabama","Alaska","Arizona","Arkansas","California","Colorado","Connecticut",
  "Delaware","Florida","Georgia","Hawaii","Idaho","Illinois","Indiana","Iowa",
  "Kansas","Kentucky","Louisiana","Maine","Maryland","Massachusetts","Michigan",
  "Minnesota","Mississippi","Missouri","Montana","Nebraska","Nevada","New Hampshire",
  "New Jersey","New Mexico","New York","North Carolina","North Dakota","Ohio",
  "Oklahoma","Oregon","Pennsylvania","Rhode Island","South Carolina","South Dakota",
  "Tennessee","Texas","Utah","Vermont","Virginia","Washington","West Virginia",
  "Wisconsin","Wyoming",
];

// [CG] step 1 is where the license gets scanned, so the label says that now (sponsor couldn't find the scan)
const STEPS = [
  { number: 1, label: "Scan License" },
  { number: 2, label: "License Details" },
  { number: 3, label: "Insurance & Vehicle" },
  { number: 4, label: "Review" },
  { number: 5, label: "Payment" },
];

function ProgressBar({ current }: { current: Step }) {
  return (
    <div className="w-full mb-10">
      <div className="flex items-center justify-between relative">
        <div
          className="absolute top-4 left-0 h-0.5 transition-all duration-500"
          style={{
            width: `${((current - 1) / (STEPS.length - 1)) * 100}%`,  // [CG] was / 3, overshot on step 5
            backgroundColor: "var(--primary)",
          }}
        />
        <div className="absolute top-4 left-0 right-0 h-0.5" style={{ backgroundColor: "var(--border)" }} />
        {STEPS.map((step) => {
          const done = current > step.number;
          const active = current === step.number;
          return (
            <div key={step.number} className="flex flex-col items-center z-10 gap-2">
              <div
                className="w-8 h-8 rounded-full flex items-center justify-center text-sm font-semibold transition-all duration-300"
                style={{
                  backgroundColor: done ? "var(--accent)" : active ? "var(--primary)" : "var(--card)",
                  color: done ? "#ffffff" : active ? "var(--primary-foreground)" : "var(--muted-foreground)",
                  border: done || active ? "none" : "2px solid var(--border)",
                }}
              >
                {done ? (
                  <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                    <path d="M2 7l3.5 3.5L12 3.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                ) : (
                  step.number
                )}
              </div>
              <span
                className="text-xs font-medium hidden sm:block"
                style={{ color: active ? "var(--primary)" : done ? "var(--accent)" : "var(--muted-foreground)", fontWeight: done ? 600 : undefined }}
              >
                {step.label}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// [CG] scan confidence per field (0-1), keyed by field name. sponsor wanted a % on autofilled fields with the low ones
// highlighted. typing in a field clears its badge since it's the customer's value now. barcode fields are always 100%
export const CONFIDENT = 0.8;  // [CG] under 80% gets highlighted
const ScanConfidence = createContext<{
  scores: Record<string, number>; clear: (name: string) => void; mark: (scores: Record<string, number>) => void; missing: string;
}>({ scores: {}, clear: () => undefined, mark: () => undefined, missing: "" });

// [CG] which fields the scan actually filled (they were blank and the scan had a value, same rule as fillBlank)
function filledByScan<T extends object>(current: T, incoming: Partial<Record<keyof T, string | undefined>>): (keyof T)[] {
  return (Object.keys(incoming) as (keyof T)[]).filter(key => !String(current[key] ?? "").trim() && incoming[key]?.trim());
}

function Field({
  label,
  required,
  children,
  hint,
  name,
}: {
  label: string;
  required?: boolean;
  children: React.ReactNode;
  hint?: string;
  name?: string;  // [CG] field name, for the confidence badge and the red "required" outline
}) {
  const { scores, clear, missing } = useContext(ScanConfidence);
  const score = name !== undefined ? scores[name] : undefined;
  const shaky = score !== undefined && score < CONFIDENT;
  const blank = name !== undefined && name === missing;
  return (
    <div className="flex flex-col gap-1.5 rounded-md" data-field={name}
      style={shaky || blank ? { outline: `2px solid ${blank ? "#b42318" : "#d97706"}`, outlineOffset: 4 } : undefined}
      onInputCapture={() => { if (name) clear(name); }}>
      <label className="text-sm font-medium flex items-center gap-2 flex-wrap" style={{ color: "var(--foreground)" }}>
        <span>
          {label}
          {required && <span style={{ color: "#e05a1a" }} className="ml-0.5">*</span>}
        </span>
        {score !== undefined && (
          <span className="text-xs font-semibold px-2 py-0.5 rounded-full"
            style={shaky ? { backgroundColor: "#fef3c7", color: "#92400e" } : { backgroundColor: "#e6f4e8", color: "#2d6636" }}>
            {shaky ? `Please check · scanned ${Math.round(score * 100)}% sure` : `Scanned · ${Math.round(score * 100)}% sure`}
          </span>
        )}
      </label>
      {children}
      {blank && <p className="text-xs font-semibold" style={{ color: "#b42318" }}>This field is required.</p>}
      {hint && <p className="text-xs" style={{ color: "var(--muted-foreground)" }}>{hint}</p>}
    </div>
  );
}

const inputClass =
  "w-full px-3 py-2.5 rounded-md text-sm outline-none transition-all duration-150";
const inputStyle = {
  border: "1.5px solid var(--border)",
  backgroundColor: "var(--card)",
  color: "var(--foreground)",
};

function InputEl({ choices, ...props }: React.InputHTMLAttributes<HTMLInputElement> & { choices?: readonly string[] }) {
  const listId = useId();
  const date = props.type === "date";
  const tel = props.type === "tel";
  const handleChange: React.ChangeEventHandler<HTMLInputElement> = (event) => {
    const raw = event.target.value;
    if (date && /^\d{4}-\d{2}-\d{2}$/.test(raw)) {
      event.target.value = raw;
    } else if (date) {
      const digits = raw.replace(/\D/g, "").slice(0, 8);
      const parts = [digits.slice(0, 2), digits.slice(2, 4), digits.slice(4)];
      event.target.value = normalizeDate(parts.filter(Boolean).join("/"));
    } else if (tel) {
      const digits = raw.replace(/\D/g, "").slice(0, 10);
      event.target.value = [digits.slice(0, 3), digits.slice(3, 6), digits.slice(6)].filter(Boolean).join("-");
    } else if (props.name === "zip") {
      const digits = raw.replace(/\D/g, "").slice(0, 9);
      event.target.value = [digits.slice(0, 5), digits.slice(5)].filter(Boolean).join("-");
    } else if (props.inputMode === "numeric") {
      event.target.value = raw.replace(/\D/g, "").slice(0, props.maxLength);
    }
    props.onChange?.(event);
  };
  return (
    <>
    <input
      {...props}
      type={date ? "text" : props.type}
      value={date ? displayDate(String(props.value ?? "")) : props.value}
      placeholder={date ? "MM/DD/YYYY" : props.placeholder}
      inputMode={date ? "numeric" : tel ? "tel" : props.inputMode}
      data-keypad={date || tel || props.name === "zip" || props.inputMode === "numeric" ? "numeric" : undefined}  // [CG] tells the touch keyboard to show the number pad
      maxLength={props.maxLength ?? (date ? 10 : tel ? 12 : undefined)}
      list={choices ? listId : props.list}
      onChange={handleChange}
      className={props.className ?? inputClass}
      style={props.style ?? inputStyle}
      onFocus={(e) => {
        e.currentTarget.style.borderColor = "var(--ring)";
        e.currentTarget.style.boxShadow = "0 0 0 3px rgba(26,58,92,0.1)";
        props.onFocus?.(e);
      }}
      onBlur={(e) => {
        e.currentTarget.style.borderColor = "var(--border)";
        e.currentTarget.style.boxShadow = "none";
        props.onBlur?.(e);
      }}
    />
    {choices && <datalist id={listId}>{choices.map(choice => <option key={choice} value={choice} />)}</datalist>}
    </>
  );
}

function SelectEl(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className={inputClass + " appearance-none cursor-pointer"}
      style={{ ...inputStyle, backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath d='M2 4l4 4 4-4' stroke='%236b7a8d' stroke-width='1.5' fill='none' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E")`, backgroundRepeat: "no-repeat", backgroundPosition: "right 12px center", paddingRight: "36px" }}
      onFocus={(e) => {
        e.currentTarget.style.borderColor = "var(--ring)";
        e.currentTarget.style.boxShadow = "0 0 0 3px rgba(26,58,92,0.1)";
        props.onFocus?.(e);
      }}
      onBlur={(e) => {
        e.currentTarget.style.borderColor = "var(--border)";
        e.currentTarget.style.boxShadow = "none";
        props.onBlur?.(e);
      }}
    />
  );
}

function Step1({
  data,
  onChange,
  onLicenseScan,
  licenseScanned,
}: {
  data: PersonalInfo;
  onChange: React.Dispatch<React.SetStateAction<PersonalInfo>>;
  onLicenseScan: (result: ScanResult) => void;
  licenseScanned: boolean;
}) {
  const [scanned, setScanned] = useState(licenseScanned);
  const [expanded, setExpanded] = useState(licenseScanned || Boolean(data.firstName || data.address));
  const { mark, missing } = useContext(ScanConfidence);
  const showAll = expanded || Boolean(missing && missing in data);  // [CG] open the fields if Continue stopped on one of them
  const set = (k: keyof PersonalInfo) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    onChange({ ...data, [k]: e.target.value });

  const handleScan = (result: ScanResult) => {
    setScanned(true);
    setExpanded(true);
    const f = result.fields;
    const incoming = {
      firstName: f.customer_first_name, lastName: f.customer_last_name,
      dateOfBirth: normalizeDate(f.date_of_birth ?? ""), address: f.street_address,
      city: f.city, state: stateName(f.state), zip: f.zip_code,
    };
    mark(Object.fromEntries(filledByScan(data, incoming).map(key => [key, 1])));  // [CG] barcode reads are exact
    onChange(current => fillBlank(current, incoming));
    onLicenseScan(result);
  };

  return (
    <div className="flex flex-col gap-6">
      <ScanPrompt
        title="Scan Your Driver's License"
        subtitle="Put your driver’s license in the scanner box. The camera will take a photo of the front, then read the barcode on the back."
        docType="license"
        scanned={scanned}
        onScan={handleScan}
        onManual={() => setExpanded(true)}
        autoOpen
      />

      {/* Contact — always manual. [CG] headings and hints reworded */}
      <div className="flex flex-col gap-5">
        <div className="flex items-center gap-3">
          <div className="h-px flex-1" style={{ backgroundColor: "var(--border)" }} />
          <span className="text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--muted-foreground)" }}>Type your email and phone</span>
          <div className="h-px flex-1" style={{ backgroundColor: "var(--border)" }} />
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
          <Field label="Email Address" required name="email" hint="Your receipt is emailed here.">
            <InputEl maxLength={120} type="email" placeholder="margaret.sullivan@email.com" value={data.email} onChange={set("email")} />
          </Field>
          <Field label="Phone Number" required name="phone">
            <InputEl type="tel" placeholder="(555) 867-5309" value={data.phone} onChange={set("phone")} />
          </Field>
        </div>
      </div>

      {/* Personal fields — pre-filled by scan, editable */}
      {showAll && (
        <div className="flex flex-col gap-5">
          <div className="flex items-center gap-3">
            <div className="h-px flex-1" style={{ backgroundColor: "var(--border)" }} />
            <span className="text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--muted-foreground)" }}>From your license — check each one</span>
            <div className="h-px flex-1" style={{ backgroundColor: "var(--border)" }} />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
            <Field label="First Name" required name="firstName">
              <InputEl maxLength={35} placeholder="Margaret" value={data.firstName} onChange={set("firstName")} />
            </Field>
            <Field label="Last Name" required name="lastName">
              <InputEl maxLength={35} placeholder="Sullivan" value={data.lastName} onChange={set("lastName")} />
            </Field>
          </div>
          <Field label="Date of Birth" required name="dateOfBirth">
            <InputEl type="date" value={data.dateOfBirth} onChange={set("dateOfBirth")} />
          </Field>
          <Field label="Registration County" required name="county" hint="The Texas county where the vehicle is registered.">
            <InputEl choices={counties} value={data.county} onChange={set("county")} placeholder="Search or enter county" maxLength={45} />
          </Field>
          <Field label="Street Address" required name="address">
            <InputEl maxLength={80} placeholder="742 Evergreen Terrace" value={data.address} onChange={set("address")} />
          </Field>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-5">
            <Field label="City" required name="city">
              <InputEl maxLength={45} placeholder="Austin" value={data.city} onChange={set("city")} />
            </Field>
            <Field label="State" required name="state">
              <SelectEl value={data.state} onChange={set("state")}>
                {data.state && !US_STATES.includes(data.state) && <option>{data.state}</option>}
                <option value="">Select</option>
                {US_STATES.map((s) => <option key={s}>{s}</option>)}
              </SelectEl>
            </Field>
            <Field label="ZIP Code" required name="zip">
              <InputEl name="zip" inputMode="numeric" placeholder="78701" value={data.zip} onChange={set("zip")} maxLength={10} />
            </Field>
          </div>
        </div>
      )}
    </div>
  );
}

type ScanState = "idle" | "scanning" | "done";

function CameraModal({
  docType,
  onCapture,
  onClose,
}: {
  docType: "license" | "insurance";
  onCapture: () => void;
  onClose: () => void;
}) {
  const [frameUrl, setFrameUrl] = useState("");
  const [camError, setCamError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    let currentUrl = "";
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      let delay = 200;
      try {
        const response = await kioskFetch("/api/camera/frame.jpg", { cache: "no-store", signal: controller.signal });
        if (!response.ok) {
          const detail = await response.json();
          throw new Error(detail.error || "Camera unavailable.");
        }
        const blob = await response.blob();
        if (controller.signal.aborted) return;
        const next = URL.createObjectURL(blob);
        setFrameUrl(next);
        if (currentUrl) URL.revokeObjectURL(currentUrl);
        currentUrl = next;
        setCamError("");
      } catch (error) {
        if (controller.signal.aborted) return;
        setFrameUrl("");
        setCamError(error instanceof Error ? error.message : "Camera unavailable.");
        delay = 1500;
      }
      if (!controller.signal.aborted) timer = setTimeout(refresh, delay);
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); if (currentUrl) URL.revokeObjectURL(currentUrl); };
  }, []);

  // [CG] license is two shots: front photo first (goes to Monday), then the barcode on the back. BV's original design
  const [frontSaved, setFrontSaved] = useState(false);
  const [savingFront, setSavingFront] = useState(false);
  const [captureError, setCaptureError] = useState("");
  const saving = useRef<AbortController | null>(null);
  useEffect(() => () => saving.current?.abort(), []);
  const handleCapture = async () => {
    if (!frameUrl || camError || saving.current) return;
    if (docType !== "license" || frontSaved) { onCapture(); return; }
    const controller = new AbortController();
    saving.current = controller;
    setSavingFront(true); setCaptureError("");
    const timer = setTimeout(() => controller.abort(), 20000);
    try {
      await saveLicenseFront(controller.signal);
      if (!controller.signal.aborted) setFrontSaved(true);
    } catch (error) {
      setCaptureError(controller.signal.aborted ? "Photo capture timed out. Please try again."
        : error instanceof Error ? error.message : "The front photo could not be saved. Please try again.");
    } finally {
      clearTimeout(timer); saving.current = null; setSavingFront(false);
    }
  };
  const handleClose = onClose;

  const [insuranceFormat, setInsuranceFormat] = useState<"paper" | "phone">("paper");
  const shape = docType === "license" ? "license" : insuranceFormat;
  const guides = {
    license: frontSaved ? {  // [CG] front/back wording for the two-step license scan
      title: "Now Flip Your License Over",
      subtitle: "Front photo saved. Turn your license over so the barcode on the back faces up.",
      position: "Step 2 of 2 · Back side", label: "Keep your whole license visible",
      detail: "Keep the full barcode visible",
      tip: "Center the license beneath the camera. Keep all four corners visible and hold it still.",
    } : {
      title: "Photograph the Front of Your License",
      subtitle: "Place your license in the box with the photo side facing up, long edge left to right.",
      position: "Step 1 of 2 · Front side", label: "Keep your whole license visible",
      detail: "Keep your photo and all front-side details visible",
      tip: "Center the license beneath the camera. Keep all four corners visible and hold it still.",
    },
    paper: {
      title: "Position Your Insurance Form",
      subtitle: "Place your letter-size form under the camera, turned sideways.",
      position: "Landscape · 8½ × 11 in paper", label: "Keep the whole page visible",
      detail: "Keep every edge visible",
      tip: "Lay the page flat. Keep the text clear, with no folds, shadows or glare.",
    },
    phone: {
      title: "Position Your Insurance Screen",
      subtitle: "Rotate your phone sideways and open your proof of insurance.",
      position: "Landscape · Phone screen", label: "Keep your whole phone screen visible",
      detail: "Keep the insurance details visible",
      tip: "Increase screen brightness and keep the display awake. Tilt slightly if there is glare.",
    },
  };
  const guide = guides[shape];

  return (
    <div role="dialog" aria-modal="true" aria-labelledby="camera-guide-title"
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backgroundColor: "rgba(0,0,0,0.82)", backdropFilter: "blur(4px)" }}>
      <div className="w-full max-w-lg max-h-[90dvh] overflow-y-auto rounded-xl p-6 flex flex-col gap-4"
        style={{ backgroundColor: "#111820", color: "#fff" }}>
        <div className="flex items-start justify-between gap-4">
          <div>
            <p id="camera-guide-title" className="font-semibold text-xl">{guide.title}</p>
            <p className="text-sm mt-2 leading-relaxed" style={{ color: "#bcc6d2" }}>{guide.subtitle}</p>
          </div>
          <button aria-label="Close scanner" onClick={handleClose}
            className="w-10 h-10 shrink-0 rounded-full flex items-center justify-center"
            style={{ backgroundColor: "#202b37", border: "1px solid #3e4855", color: "#fff" }}>
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
              <path d="M2 2l10 10M12 2L2 12" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
            </svg>
          </button>
        </div>

        {docType === "insurance" && (
          <div className="flex gap-2" role="group" aria-label="Insurance document format">
            {(["paper", "phone"] as const).map(format => (
              <button key={format} type="button" aria-pressed={insuranceFormat === format}
                onClick={() => setInsuranceFormat(format)} className="flex-1 rounded-lg px-3 py-3 text-sm font-medium"
                style={{ border: `1px solid ${insuranceFormat === format ? "var(--accent)" : "#465260"}`,
                  color: insuranceFormat === format ? "#fff" : "#cbd4df",
                  backgroundColor: insuranceFormat === format ? "#30261f" : "#202b37" }}>
                {format === "paper" ? "Paper form" : "Phone screen"}
              </button>
            ))}
          </div>
        )}

        <p className="text-xs uppercase tracking-widest text-center mt-1" style={{ color: "#bdc7d3" }}>{guide.position}</p>
        <div className="relative shrink-0" style={{ aspectRatio: "16/9", backgroundColor: "#090e15" }}>
          {camError || !frameUrl ? (
            <div role="status" className="absolute inset-0 flex items-center justify-center p-6 text-center text-sm" style={{ color: "#cbd4df" }}>
              {camError || "Starting the Pi camera…"}
            </div>
          ) : (
            <img src={frameUrl} alt="Live Arducam preview" className="absolute inset-0 w-full h-full object-contain" />
          )}
        </div>
        <div className="text-center">
          <p className="text-base font-medium">{guide.label}</p>
          <p className="text-xs mt-1" style={{ color: "#a6b4c4" }}>{guide.detail}</p>
          <p className="text-sm mt-3 leading-relaxed" style={{ color: "#bcc6d2" }}>{guide.tip}</p>
        </div>

        <div className="flex gap-3">
          <button onClick={handleClose} className="flex-1 py-3 rounded-lg text-sm font-medium"
            style={{ backgroundColor: "#202b37", color: "#e2e8ef", border: "1px solid #455260" }}>Cancel</button>
          <button onClick={() => void handleCapture()} disabled={!frameUrl || Boolean(camError) || savingFront}
            className="flex-[2] py-3 rounded-lg text-sm font-semibold disabled:opacity-40 flex items-center justify-center gap-2"
            style={{ backgroundColor: "var(--accent)", color: "#fff" }}>
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
              <circle cx="9" cy="9" r="6" stroke="currentColor" strokeWidth="1.8" />
              <circle cx="9" cy="9" r="3" fill="currentColor" />
            </svg>
            {savingFront ? "Saving photo…" : docType === "license" && !frontSaved ? "Take Front Photo" : "Capture & Scan"}
          </button>
        </div>
        {captureError && <p role="alert" className="text-sm text-red-300">{captureError}</p>}
        <p className="text-xs text-center" style={{ color: "#aab8c7" }}>
          {docType === "license" && !frontSaved ? "Hold still, then select Take Front Photo." : "Hold still, then select Capture & Scan."}
        </p>
      </div>
    </div>
  );
}

function ScanPrompt({
  title,
  subtitle,
  docType,
  scanned,
  onScan,
  onManual,
  autoOpen,
}: {
  title: string;
  subtitle: string;
  docType: "license" | "insurance";
  scanned: boolean;
  onScan: (result: ScanResult) => void;
  onManual?: () => void;
  autoOpen?: boolean;  // [CG] camera opens on its own when the step loads
}) {
  const [state, setState] = useState<ScanState>(scanned ? "done" : "idle");
  const [cameraOpen, setCameraOpen] = useState(false);
  // [CG] opening the license scanner also triggers the security Pi's license_scan photo
  const openCamera = () => {
    setCameraOpen(true);
    if (docType === "license") void requestSecurityPhoto();
  };
  const autoOpened = useRef(false);
  useEffect(() => {
    if (autoOpen && !scanned && !autoOpened.current) { autoOpened.current = true; openCamera(); }
  }, []);  // [CG] only on first load of the step

  const [error, setError] = useState("");
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => { pending.current?.abort(); }, []);

  const handleCapture = async () => {
    if (pending.current) return;
    setCameraOpen(false);
    setError("");
    setState("scanning");
    const controller = new AbortController();
    pending.current = controller;
    let timedOut = false;
    const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 120000);
    try {
      const result = await requestScan(docType, controller.signal);
      if (controller.signal.aborted) return;
      onScan(result);
      setState("done");
    } catch (err) {
      if (controller.signal.aborted && !timedOut) return;
      setError(timedOut ? "Scan timed out. Wait for processing to finish, then retry or enter the fields manually." : err instanceof Error ? err.message : "Scan failed. Please try again.");
      setState("idle");
      onManual?.();
    } finally {
      clearTimeout(timeout);
      if (pending.current === controller) pending.current = null;
    }
  };

  return (
    <>
      {cameraOpen && (
        <CameraModal
          docType={docType}
          onCapture={handleCapture}
          onClose={() => { setCameraOpen(false); onManual?.(); }}
        />
      )}

      <div
        className="rounded-xl overflow-hidden transition-all duration-300"
        style={{ border: `2px ${state === "done" ? "solid" : "dashed"} ${state === "done" ? "#3a7d44" : "var(--border)"}`, backgroundColor: state === "done" ? "#f0f7ee" : "var(--card)" }}
      >
        {/* [CG] bigger prompt and button so the scan step is easy to spot */}
        {state === "idle" && (
          <div className="flex flex-col items-center gap-4 px-6 py-10 text-center">
            <div className="w-20 h-20 rounded-2xl flex items-center justify-center" style={{ backgroundColor: "var(--secondary)" }}>
              <svg width="40" height="40" viewBox="0 0 24 24" fill="none" style={{ color: "var(--primary)" }}>
                <rect x="2" y="6" width="20" height="14" rx="2" stroke="currentColor" strokeWidth="1.5" />
                <circle cx="12" cy="13" r="3.5" stroke="currentColor" strokeWidth="1.5" />
                <path d="M8 6l1.2-2.4A1 1 0 0110.1 3h3.8a1 1 0 01.9.6L16 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                <circle cx="18.5" cy="9.5" r="1" fill="currentColor" />
              </svg>
            </div>
            <div>
              <p className="font-semibold text-xl mb-1" style={{ color: "var(--foreground)" }}>{title}</p>
              <p className="text-sm leading-relaxed" style={{ color: error ? "#b42318" : "var(--muted-foreground)" }} role={error ? "alert" : undefined}>{error || subtitle}</p>
            </div>
            <button
              onClick={openCamera}
              className="flex items-center justify-center gap-2 px-10 py-4 rounded-lg text-base font-semibold transition-all duration-150"
              style={{ backgroundColor: "var(--accent)", color: "#ffffff" }}
              onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = "#d95c1a"; }}
              onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = "var(--accent)"; }}
            >
              <svg width="20" height="20" viewBox="0 0 16 16" fill="none">
                <rect x="1" y="3" width="14" height="10" rx="1.5" stroke="currentColor" strokeWidth="1.4" />
                <circle cx="8" cy="8" r="2.5" stroke="currentColor" strokeWidth="1.4" />
                <circle cx="12.5" cy="5.5" r="1" fill="currentColor" />
              </svg>
              {error ? "Try the Scanner Again" : "Open the Scanner"}
            </button>
          </div>
        )}

        {state === "scanning" && (
          <div className="flex flex-col items-center gap-4 px-6 py-8 relative overflow-hidden">
            <div className="flex items-center gap-3">
              <svg width="20" height="20" viewBox="0 0 20 20" fill="none" className="animate-spin" style={{ color: "var(--primary)" }}>
                <circle cx="10" cy="10" r="8" stroke="currentColor" strokeWidth="2" strokeOpacity="0.2" />
                <path d="M10 2a8 8 0 018 8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              </svg>
              <span className="text-base font-medium" style={{ color: "var(--primary)" }}>Reading your document… please wait</span>
            </div>
            <style>{`@keyframes scanline { 0%{top:10%} 100%{top:90%} }`}</style>
            <div className="absolute inset-x-6 top-4 bottom-4 pointer-events-none rounded-lg overflow-hidden" style={{ border: "1px solid rgba(26,58,92,0.15)" }}>
              <div className="absolute inset-x-0 h-0.5" style={{ backgroundColor: "var(--accent)", opacity: 0.8, animation: "scanline 1.2s ease-in-out infinite alternate", top: "50%" }} />
            </div>
          </div>
        )}

        {state === "done" && (
          <div className="flex items-center gap-4 px-5 py-4">
            <div className="w-10 h-10 rounded-full flex items-center justify-center shrink-0" style={{ backgroundColor: "#3a7d44" }}>
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                <path d="M3 8l3.5 3.5 6.5-6.5" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold" style={{ color: "#2d6636" }}>{docType === "license" ? "Remove your driver’s license" : "Remove your insurance card"}</p>
              <p className="text-sm" style={{ color: "#3a7d44" }}>Check the details below.</p>
            </div>
            <button
              onClick={() => { setState("idle"); }}
              className="text-xs underline underline-offset-2 shrink-0"
              style={{ color: "var(--muted-foreground)" }}
            >
              Rescan
            </button>
          </div>
        )}
      </div>
    </>
  );
}

function Step2({
  data,
  onChange,
}: {
  data: DriversLicense;
  onChange: (d: DriversLicense) => void;
}) {
  const set = (k: keyof DriversLicense) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    onChange({ ...data, [k]: e.target.value });

  return (
    <div className="flex flex-col gap-6">
      <div
        className="rounded-lg p-5 flex flex-col gap-5"
        style={{ border: "1px solid var(--border)", backgroundColor: "var(--card)" }}
      >
        <p className="text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--muted-foreground)" }}>Driver's License</p>
        {/* [CG] */}
        <p className="text-sm" style={{ color: "var(--muted-foreground)" }}>These came from your license's barcode. Fix anything that doesn't match your card.</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
          <Field label="License Number" required name="licenseNumber">
            <InputEl maxLength={24} placeholder="D123-4567-8901" value={data.licenseNumber} onChange={set("licenseNumber")} />
          </Field>
          <Field label="Issuing State" required name="licenseState">
            <SelectEl value={data.licenseState} onChange={set("licenseState")}>
                {data.licenseState && !US_STATES.includes(data.licenseState) && <option>{data.licenseState}</option>}
              <option value="">Select</option>
              {US_STATES.map((s) => <option key={s}>{s}</option>)}
            </SelectEl>
          </Field>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
          <Field label="Expiration Date" required name="expirationDate">
            <InputEl type="date" value={data.expirationDate} onChange={set("expirationDate")} />
          </Field>
          <Field label="License Class" required name="licenseClass">
            <SelectEl value={data.licenseClass} onChange={set("licenseClass")}>
            {data.licenseClass && !["A", "B", "C", "D", "M"].includes(data.licenseClass) && <option>{data.licenseClass}</option>}
              <option value="">Select class</option>
              <option value="A">Class A — Commercial</option>
              <option value="B">Class B — Commercial</option>
              <option value="C">Class C — Standard</option>
              <option value="D">Class D — Non-commercial</option>
              <option value="M">Class M — Motorcycle</option>
            </SelectEl>
          </Field>
        </div>
      </div>

    </div>
  );
}

function Step3({
  data,
  onChange,
  vehicleData,
  onVehicleChange,
}: {
  data: InsuranceInfo;
  onChange: React.Dispatch<React.SetStateAction<InsuranceInfo>>;
  vehicleData: DriversLicense;
  onVehicleChange: React.Dispatch<React.SetStateAction<DriversLicense>>;
}) {
  const [insuranceScanned, setInsuranceScanned] = useState(false);
  const set = (k: keyof InsuranceInfo) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    onChange({ ...data, [k]: e.target.value });
  const setVehicle = (k: keyof DriversLicense) => (e: React.ChangeEvent<HTMLInputElement>) =>
    onVehicleChange({ ...vehicleData, [k]: e.target.value });

  const { mark } = useContext(ScanConfidence);
  const handleInsuranceScan = (result: ScanResult) => {
    setInsuranceScanned(true);
    const f = result.fields;
    const insuranceIn = { provider: f.insurance_company, policyNumber: f.policy_number, endDate: normalizeDate(f.insurance_expiration ?? "") };
    const vehicleIn = {
      vehicleYear: f.vehicle_year, vehicleMake: f.vehicle_make, vehicleModel: f.vehicle_model,
      vin: f.vehicle_vin?.length === 17 ? f.vehicle_vin : undefined,
    };
    // [CG] map the reader's field names to ours so the confidence lines up
    const reader: Record<string, string> = { provider: "insurance_company", policyNumber: "policy_number", endDate: "insurance_expiration",
      vehicleYear: "vehicle_year", vehicleMake: "vehicle_make", vehicleModel: "vehicle_model", vin: "vehicle_vin" };
    const filled = [...filledByScan(data, insuranceIn), ...filledByScan(vehicleData, vehicleIn)] as string[];
    mark(Object.fromEntries(filled.map(key => [key, result.confidence?.[reader[key]] ?? 0.5])));  // [CG] no score = treat as unsure
    onChange(current => fillBlank(current, insuranceIn));
    onVehicleChange(current => fillBlank(current, vehicleIn));
  };

  return (
    <div className="flex flex-col gap-6">
      {/* [CG] camera opens on its own; wording updated */}
      <ScanPrompt
        title="Scan Proof of Insurance"
        subtitle="Put your insurance card, or your phone showing your insurance, in the scanner box."
        docType="insurance"
        scanned={insuranceScanned}
        onScan={handleInsuranceScan}
        autoOpen
      />
      <div
        className="rounded-lg p-5 flex flex-col gap-5"
        style={{ border: "1px solid var(--border)", backgroundColor: "var(--card)" }}
      >
        <p className="text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--muted-foreground)" }}>Vehicle Information</p>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-5">
          <Field label="Year" required name="vehicleYear">
            <InputEl inputMode="numeric" placeholder="2019" value={vehicleData.vehicleYear} onChange={setVehicle("vehicleYear")} maxLength={4} />
          </Field>
          <Field label="Make" required name="vehicleMake">
            <InputEl choices={makes} maxLength={55} placeholder="Search or enter make" value={vehicleData.vehicleMake} onChange={setVehicle("vehicleMake")} />
          </Field>
          <Field label="Model" required name="vehicleModel">
            <InputEl choices={vehicleModels(vehicleData.vehicleMake)} maxLength={60} placeholder="Search or enter model" value={vehicleData.vehicleModel} onChange={setVehicle("vehicleModel")} />
          </Field>
        </div>
        <Field label="VIN" required name="vin" hint="17 characters. It's on your insurance card and at the bottom of the windshield on the driver's side.">
          <InputEl placeholder="1HGBH41JXMN109186" value={vehicleData.vin} onChange={setVehicle("vin")} maxLength={17} />
        </Field>
        <Field label="Current License Plate" required name="licensePlate">
          <InputEl maxLength={12} placeholder="ABC-1234" value={vehicleData.licensePlate} onChange={setVehicle("licensePlate")} />
        </Field>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
        <Field label="Insurance Provider" required name="provider">
          <InputEl choices={providers} value={data.provider} onChange={set("provider")} placeholder="Search or enter provider" maxLength={90} />
        </Field>
        <Field label="Policy Number" required name="policyNumber">
          <InputEl placeholder="POL-8821049-A" value={data.policyNumber} onChange={set("policyNumber")} />
        </Field>
      </div>
      <Field label="Policy Holder Name" required name="policyHolder">
        <InputEl maxLength={70} placeholder="Margaret Sullivan" value={data.policyHolder} onChange={set("policyHolder")} />
      </Field>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
        <Field label="Coverage Start Date (optional)">
          <InputEl type="date" value={data.startDate} onChange={set("startDate")} />
        </Field>
        <Field label="Policy End Date" required name="endDate">
          <InputEl type="date" value={data.endDate} onChange={set("endDate")} />
        </Field>
      </div>
      <Field label="Policy Type (optional)">
        <SelectEl value={data.coverageType} onChange={set("coverageType")}>
          <option value="">Select coverage</option>
          <option value="liability">Liability Only</option>
          <option value="collision">Collision</option>
          <option value="comprehensive">Comprehensive</option>
          <option value="full">Full Coverage (Comprehensive + Collision)</option>
          <option value="uninsured">Uninsured Motorist</option>
        </SelectEl>
      </Field>
      <div
        className="rounded-lg p-5 flex flex-col gap-5"
        style={{ border: "1px solid var(--border)", backgroundColor: "var(--card)" }}
      >
        <p className="text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--muted-foreground)" }}>Agent Information <span className="normal-case font-normal">(optional)</span></p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
          <Field label="Agent Name">
            <InputEl maxLength={70} placeholder="Robert Chen" value={data.agentName} onChange={set("agentName")} />
          </Field>
          <Field label="Agent Phone">
            <InputEl type="tel" placeholder="(555) 234-5678" value={data.agentPhone} onChange={set("agentPhone")} />
          </Field>
        </div>
      </div>
    </div>
  );
}

function EditableRow({
  label,
  value,
  onChange,
  type = "text",
  options,
  choices,
  maxLength,
  inputMode,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  type?: string;
  options?: string[];
  choices?: readonly string[];
  maxLength?: number;
  inputMode?: "numeric";
}) {
  const [editing, setEditing] = useState(false);

  return (
    <div className="flex justify-between items-center py-2.5 gap-4" style={{ borderBottom: "1px solid var(--border)" }}>
      <span className="text-sm shrink-0" style={{ color: "var(--muted-foreground)" }}>{label}</span>
      <div className="flex items-center gap-2 min-w-0">
        {editing ? (
          <>
            {options ? (
              <select
                autoFocus
                value={value}
                onChange={(e) => onChange(e.target.value)}
                onBlur={() => setEditing(false)}
                className="text-sm rounded px-2 py-1 outline-none"
                style={{ border: "1.5px solid var(--accent)", backgroundColor: "var(--card)", color: "var(--foreground)", fontFamily: "'Instrument Sans', sans-serif", maxWidth: 200 }}
              >
                {options.map((o) => <option key={o}>{o}</option>)}
              </select>
            ) : (
              <InputEl
                autoFocus
                aria-label={label}
                name={label === "ZIP" ? "zip" : undefined}
                choices={choices}
                maxLength={maxLength}
                inputMode={inputMode}
                type={type}
                value={value}
                onChange={(e) => onChange(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && setEditing(false)}
                className="text-sm rounded px-2 py-1 outline-none text-right"
                style={{ border: "1.5px solid var(--accent)", backgroundColor: "var(--card)", color: "var(--foreground)", fontFamily: "'Instrument Sans', sans-serif", minWidth: 0, maxWidth: 200 }}
              />
            )}
            <button
              onClick={() => setEditing(false)}
              className="shrink-0 text-xs font-semibold px-2 py-1 rounded transition-colors"
              style={{ backgroundColor: "var(--accent)", color: "#fff" }}
            >
              Done
            </button>
          </>
        ) : (
          <>
            <span className="text-sm font-medium text-right truncate" style={{ color: value ? "var(--foreground)" : "var(--muted-foreground)" }}>
              {(type === "date" ? displayDate(value) : value) || "—"}
            </span>
            <button
              onClick={() => setEditing(true)}
              className="shrink-0 flex items-center gap-1 text-xs px-2 py-1 rounded transition-all"
              style={{ color: "var(--muted-foreground)", border: "1px solid var(--border)", backgroundColor: "transparent" }}
              onMouseEnter={(e) => { e.currentTarget.style.borderColor = "var(--accent)"; e.currentTarget.style.color = "var(--accent)"; }}
              onMouseLeave={(e) => { e.currentTarget.style.borderColor = "var(--border)"; e.currentTarget.style.color = "var(--muted-foreground)"; }}
            >
              <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
                <path d="M7 1.5l1.5 1.5L3 8.5H1.5V7L7 1.5z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
              </svg>
              Edit
            </button>
          </>
        )}
      </div>
    </div>
  );
}

function EditableSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)", backgroundColor: "var(--card)" }}>
      <div className="px-5 py-3 flex items-center gap-2" style={{ backgroundColor: "var(--secondary)" }}>
        <p className="text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--primary)" }}>{title}</p>
        <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>— tap Edit to correct any field</span>
      </div>
      <div className="px-5 pb-1">{children}</div>
    </div>
  );
}

function Step4({
  personal,
  license,
  insurance,
  onPersonalChange,
  onLicenseChange,
  onInsuranceChange,
}: {
  personal: PersonalInfo;
  license: DriversLicense;
  insurance: InsuranceInfo;
  onPersonalChange: (d: PersonalInfo) => void;
  onLicenseChange: (d: DriversLicense) => void;
  onInsuranceChange: (d: InsuranceInfo) => void;
}) {
  const sp = <K extends keyof PersonalInfo>(k: K) => (v: string) => onPersonalChange({ ...personal, [k]: v });
  const sl = <K extends keyof DriversLicense>(k: K) => (v: string) => onLicenseChange({ ...license, [k]: v });
  const si = <K extends keyof InsuranceInfo>(k: K) => (v: string) => onInsuranceChange({ ...insurance, [k]: v });

  return (
    <div className="flex flex-col gap-5">
      <div className="rounded-lg p-3 flex gap-2 items-center" style={{ backgroundColor: "#fffbea", border: "1px solid #f5d76e" }}>
        <svg width="15" height="15" viewBox="0 0 15 15" fill="none" className="shrink-0">
          <path d="M7.5 2L13.5 13H1.5L7.5 2z" stroke="#b45309" strokeWidth="1.3" strokeLinejoin="round" fill="none" />
          <path d="M7.5 6v3.5M7.5 11h.01" stroke="#b45309" strokeWidth="1.3" strokeLinecap="round" />
        </svg>
        <p className="text-xs" style={{ color: "#92400e" }}>Review carefully — hit <strong>Edit</strong> on any field to fix scanner errors before proceeding to payment.</p>
      </div>

      <EditableSection title="Personal Information">
        <EditableRow maxLength={35} label="First Name"    value={personal.firstName}   onChange={sp("firstName")} />
        <EditableRow maxLength={35} label="Last Name"     value={personal.lastName}    onChange={sp("lastName")} />
        <EditableRow label="Date of Birth" value={personal.dateOfBirth} onChange={sp("dateOfBirth")} type="date" />
        <EditableRow maxLength={120} label="Email"         value={personal.email}       onChange={sp("email")} type="email" />
        <EditableRow label="Phone"         value={personal.phone}       onChange={sp("phone")} type="tel" />
        <EditableRow maxLength={80} label="Address"       value={personal.address}     onChange={sp("address")} />
        <EditableRow maxLength={45} label="City"          value={personal.city}        onChange={sp("city")} />
        <EditableRow label="State"         value={personal.state}       onChange={sp("state")} options={US_STATES} />
        <EditableRow label="Registration County" value={personal.county} onChange={sp("county")} choices={counties} maxLength={45} />
        <EditableRow maxLength={10} inputMode="numeric" label="ZIP"           value={personal.zip}         onChange={sp("zip")} />
      </EditableSection>

      <EditableSection title="Driver's License">
        <EditableRow maxLength={24} label="License Number" value={license.licenseNumber}  onChange={sl("licenseNumber")} />
        <EditableRow label="Issuing State"  value={license.licenseState}   onChange={sl("licenseState")} options={US_STATES} />
        <EditableRow label="Expiration"     value={license.expirationDate} onChange={sl("expirationDate")} type="date" />
        <EditableRow label="Class"          value={license.licenseClass}   onChange={sl("licenseClass")} options={["A","B","C","D","M"]} />
        <EditableRow maxLength={4} inputMode="numeric" label="Year"           value={license.vehicleYear}    onChange={sl("vehicleYear")} />
        <EditableRow maxLength={55} choices={makes} label="Make"           value={license.vehicleMake}    onChange={sl("vehicleMake")} />
        <EditableRow maxLength={60} choices={vehicleModels(license.vehicleMake)} label="Model"          value={license.vehicleModel}   onChange={sl("vehicleModel")} />
        <EditableRow maxLength={17} label="VIN"            value={license.vin}            onChange={sl("vin")} />
        <EditableRow maxLength={12} label="License Plate"  value={license.licensePlate}   onChange={sl("licensePlate")} />
      </EditableSection>

      <EditableSection title="Insurance">
        <EditableRow maxLength={90} choices={providers} label="Provider"      value={insurance.provider}     onChange={si("provider")} />
        <EditableRow label="Policy Number" value={insurance.policyNumber} onChange={si("policyNumber")} />
        <EditableRow maxLength={70} label="Policy Holder" value={insurance.policyHolder} onChange={si("policyHolder")} />
        <EditableRow label="Start Date (optional)"    value={insurance.startDate}    onChange={si("startDate")} type="date" />
        <EditableRow label="End Date"      value={insurance.endDate}      onChange={si("endDate")} type="date" />
        <EditableRow label="Policy Type (optional)" value={insurance.coverageType} onChange={si("coverageType")} options={["","liability","collision","comprehensive","full","uninsured"]} />
      </EditableSection>

      <div className="rounded-lg p-4 flex gap-3 items-start" style={{ backgroundColor: "#f0f7ee", border: "1px solid #b8ddb4" }}>
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" className="mt-0.5 shrink-0">
          <circle cx="9" cy="9" r="8" fill="#3a7d44" />
          <path d="M5.5 9l2.5 2.5 4.5-4.5" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <p className="text-sm" style={{ color: "#2d6636" }}>
          By submitting this form you certify that all information provided is accurate and complete to the best of your knowledge.
        </p>
      </div>
    </div>
  );
}

function AdminLogin({ onLogin }: { onLogin: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(false);
  const [shaking, setShaking] = useState(false);

  const [busy, setBusy] = useState(false);
  const attempt = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const response = await kioskFetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (!response.ok) throw new Error("Login failed");
      onLogin();
    } catch {
      setError(true);
      setShaking(true);
      setTimeout(() => setShaking(false), 500);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="kiosk-ui min-h-screen flex flex-col items-center justify-center px-4" style={{ backgroundColor: "var(--primary)" }}>
      <style>{`@keyframes shake { 0%,100%{transform:translateX(0)} 20%,60%{transform:translateX(-6px)} 40%,80%{transform:translateX(6px)} }`}</style>
      <div className="w-full max-w-sm flex flex-col items-center gap-8">
        <div className="flex flex-col items-center gap-3">
          <div className="bg-white rounded-2xl p-3 shadow-lg">
            <img src={logoUrl} alt="Registration Nation" className="h-14 w-auto" />
          </div>
          <div className="text-center">
            <p className="text-white font-semibold text-lg" style={{ fontFamily: "'DM Serif Display', serif" }}>Texas Tag & Title</p>
            <p className="text-xs mt-0.5" style={{ color: "rgba(255,255,255,0.6)" }}>Staff Access Only</p>
          </div>
        </div>

        <div
          className="w-full rounded-2xl p-7 flex flex-col gap-5"
          style={{
            backgroundColor: "rgba(255,255,255,0.07)",
            border: "1px solid rgba(255,255,255,0.12)",
            backdropFilter: "blur(8px)",
            animation: shaking ? "shake 0.5s ease" : "none",
          }}
        >
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-semibold uppercase tracking-widest" style={{ color: "rgba(255,255,255,0.6)" }}>Username</label>
            <input
              type="text"
              value={username}
              autoComplete="username"
              onChange={(e) => { setUsername(e.target.value); setError(false); }}
              onKeyDown={(e) => e.key === "Enter" && attempt()}
              placeholder="Enter username"
              className="w-full px-4 py-3 rounded-lg text-sm outline-none"
              style={{ backgroundColor: "rgba(255,255,255,0.1)", border: `1.5px solid ${error ? "#f87171" : "rgba(255,255,255,0.2)"}`, color: "#ffffff", fontFamily: "'Instrument Sans', sans-serif" }}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-semibold uppercase tracking-widest" style={{ color: "rgba(255,255,255,0.6)" }}>Password</label>
            <input
              type="password"
              value={password}
              autoComplete="current-password"
              onChange={(e) => { setPassword(e.target.value); setError(false); }}
              onKeyDown={(e) => e.key === "Enter" && attempt()}
              placeholder="Enter password"
              className="w-full px-4 py-3 rounded-lg text-sm outline-none"
              style={{ backgroundColor: "rgba(255,255,255,0.1)", border: `1.5px solid ${error ? "#f87171" : "rgba(255,255,255,0.2)"}`, color: "#ffffff", fontFamily: "'Instrument Sans', sans-serif" }}
            />
          </div>

          {error && (
            <p className="text-sm text-center" style={{ color: "#fca5a5" }}>Incorrect username or password.</p>
          )}

          <button
            onClick={attempt}
            disabled={busy}
            className="w-full py-3 rounded-lg text-sm font-semibold transition-all duration-150"
            style={{ backgroundColor: "var(--accent)", color: "#ffffff" }}
            onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = "#d95c1a"; }}
            onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = "var(--accent)"; }}
          >
            Sign In
          </button>
        </div>

        <p className="text-xs text-center" style={{ color: "rgba(255,255,255,0.35)" }}>
          Registration Nation · Texas Tag & Title<br />Authorized personnel only
        </p>
      </div>
    </div>
  );
}

export default function App() {
  const [authed, setAuthed] = useState(false);
  const [step, setStep] = useState<Step>(1);
  const [submitted, setSubmitted] = useState(false);
  const [initials, setInitials] = useState("");
  const [printResult, setPrintResult] = useState<PrintStatus | null>(null);
  const [printNotice, setPrintNotice] = useState("");
  const [printTimedOut, setPrintTimedOut] = useState(false);  // [CG] printer never finished, move on anyway
  const [checkingSession, setCheckingSession] = useState(true);
  const [staff, setStaff] = useState<StaffUser | null>(null);  // [CG] for the sign-out bar
  const [signingOut, setSigningOut] = useState(false);
  const [staffMessage, setStaffMessage] = useState("");
  const [formError, setFormError] = useState("");  // [CG] on-page message, pop-ups lock up the touchscreen
  const [missing, setMissing] = useState("");  // [CG] the blank field Continue stopped at
  const [scores, setScores] = useState<Record<string, number>>({});  // [CG] scan confidence per field
  const confidence = {
    scores, missing,
    clear: (name: string) => {
      setScores(current => { if (!(name in current)) return current; const next = { ...current }; delete next[name]; return next; });
      setMissing(current => current === name ? "" : current);
      setFormError("");
    },
    mark: (incoming: Record<string, number>) => setScores(current => ({ ...current, ...incoming })),
  };
  useEffect(() => {
    const controller = new AbortController();
    const expire = () => { setAuthed(false); setStaff(null); };
    window.addEventListener("kiosk-session-expired", expire);
    staffSession(controller.signal)
      .then(user => { if (!controller.signal.aborted) { setAuthed(Boolean(user)); setStaff(user); } })
      .catch(() => {})
      .finally(() => { if (!controller.signal.aborted) setCheckingSession(false); });
    return () => {
      controller.abort();
      window.removeEventListener("kiosk-session-expired", expire);
    };
  }, []);

  // [CG] staff sign-out. blocked mid-renewal so a paid customer's receipt doesn't get lost
  const handleSignOut = async () => {
    if (sessionStorage.getItem("kiosk-payment-reference")) {
      setStaffMessage("Complete the current payment and receipt, then start a new renewal before signing out.");
      return;
    }
    setSigningOut(true); setStaffMessage("");
    try {
      await signOut();
      setAuthed(false); setStaff(null);
    } catch (error) {
      setStaffMessage(error instanceof Error ? error.message : "Please try signing out again.");
    } finally {
      setSigningOut(false);
    }
  };


  const cardEntry = useRef<CardEntry | null>(null);
  const [paymentReady, setPaymentReady] = useState(false);

  // [CG] payment method: the S700 card reader is the only one. The kiosk has no keyboard to type a card with, so there is
  // no typed-card backup; a reader problem shows on the card reader step, with Retry connecting

  // monday.com crm record, shown on the success screen
  // [CG] still tracked, just not shown anymore (the server logs failed saves)
  const [, setCrmStatus] = useState<"idle" | "saving" | "saved" | "failed">("idle");
  const [, setCrmMessage] = useState("");
  const crmSavedRef = useRef(false);  // so a print retry can never make a second crm record
  const crmPayloadRef = useRef<{ itemName: string; fields: Record<string, string | number> } | null>(null);  // kept so the retry button can resend it

  const [personal, setPersonal] = useState<PersonalInfo>({
    firstName: "", lastName: "", dateOfBirth: "", email: "", phone: "",
    address: "", city: "", state: "", zip: "", county: "",
  });
  const [license, setLicense] = useState<DriversLicense>({
    licenseNumber: "", licenseState: "", expirationDate: "", licenseClass: "",
    vehicleYear: "", vehicleMake: "", vehicleModel: "", vin: "", licensePlate: "",
  });
  const [licensePrefilled, setLicensePrefilled] = useState(false);

  const handleLicenseScan = (result: ScanResult) => {
    setLicensePrefilled(true);
    const f = result.fields;
    setLicense(current => fillBlank(current, {
      licenseNumber: f.license_number, licenseState: stateName(f.state),
      expirationDate: normalizeDate(f.license_expiration ?? ""), licenseClass: f.license_class,
    }));
  };
  const [insurance, setInsurance] = useState<InsuranceInfo>({
    provider: "", policyNumber: "", policyHolder: "", startDate: "",
    endDate: "", coverageType: "", agentName: "", agentPhone: "",
  });

  const [printing, setPrinting] = useState(false);
  const [paid, setPaid] = useState<PaymentResult | null>(null);
  const showingPrint = submitted || (printing && Boolean(paid));
  useEffect(() => { window.scrollTo(0, 0); setFormError(""); setMissing(""); }, [step, showingPrint]);
  const [paymentStatus, setPaymentStatus] = useState("");
  const printPending = useRef(false);
  const printReference = useRef(sessionStorage.getItem("kiosk-payment-reference") || "");
  useEffect(() => {
    if (!submitted || !authed) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const started = Date.now();
    // [CG] give up after 2 min with no "finished" from the printer: send the customer to staff and let the screen reset,
    // so one stuck print job can't freeze the kiosk
    const giveUp = setTimeout(() => setPrintTimedOut(true), 120000);
    const poll = async () => {
      try {
        const result = await printStatus(printReference.current, controller.signal);
        if (controller.signal.aborted) return;
        setPrintResult(result);
        setPrintNotice(Date.now() - started > 45000 && result.state !== "completed"
          ? "Still waiting for the printer. Ask a staff member for help." : "");
        if (["cancelled", "aborted", "unknown", "failed", "uncertain"].includes(result.state) ||
          (result.state === "completed" && ["sent", "failed", "uncertain", "skipped"].includes(result.email_state))) return;
      } catch {
        if (controller.signal.aborted) return;
        setPrintNotice("Checking the printer. Ask a staff member if you need help.");
      }
      timer = setTimeout(poll, 1500);
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); clearTimeout(giveUp); };
  }, [submitted, authed]);

  // builds what we send to monday.com. the server only forwards the fields listed in COLUMN_MAP (monday_service_CG.py)
  // [CG] builds the record that goes to Monday.com (the field choices were agreed with the sponsor)
  const buildCrmPayload = (confirmation: string, paymentIntentId: string) => {
    const fullName = `${personal.firstName} ${personal.lastName}`.trim();
    const plate = license.licensePlate.trim();
    const cityStateZip = [personal.city, `${personal.state} ${personal.zip}`.trim()].filter(Boolean).join(", ");
    return {
      itemName: fullName ? `${personal.lastName}, ${personal.firstName}${plate ? ` - ${plate}` : ""}` : `Renewal ${confirmation}`,
      fields: {
        fullName,
        firstName: personal.firstName,
        lastName: personal.lastName,
        email: personal.email,
        phone: personal.phone,
        dateOfBirth: personal.dateOfBirth,
        address: personal.address,
        city: personal.city,
        state: personal.state,
        zip: personal.zip,
        county: personal.county,  // [CG] column comes from MONDAY_COUNTY_COLUMN
        fullAddress: [personal.address, cityStateZip].filter(Boolean).join(", "),
        licenseNumber: license.licenseNumber,
        licensePlate: plate,
        vin: license.vin,
        vehicleYear: license.vehicleYear,
        vehicleMake: license.vehicleMake,
        vehicleModel: license.vehicleModel,
        vehicle: `${license.vehicleYear} ${license.vehicleMake} ${license.vehicleModel}`.trim(),
        insuranceProvider: insurance.provider,
        policyNumber: insurance.policyNumber,
        insuranceExpiration: insurance.endDate,
        service: "Vehicle Registration Renewal",
        amountPaid: (PAYMENT_AMOUNT / 100).toFixed(2),
        confirmationNumber: confirmation,
        paymentIntentId,
        paidDate: new Date().toISOString().slice(0, 10),
        status: "Paid",
      } as Record<string, string | number>,
    };
  };

  // sends the record to monday.com and updates the status shown on the success screen
  // [CG] sends the record to Monday.com and shows the result on the final screen
  const saveToCrm = async (payload: { itemName: string; fields: Record<string, string | number> }) => {
    crmPayloadRef.current = payload;
    setCrmStatus("saving");
    setCrmMessage("");
    const result = await createMondayRecord(payload.itemName, payload.fields);
    if (result.success) {
      crmSavedRef.current = true;
      setCrmStatus("saved");
      setCrmMessage(result.warnings.length ? `Saved, but skipped: ${result.warnings.join("; ")}` : "");
    } else {
      setCrmStatus("failed");
      setCrmMessage(result.error);
    }
  };

  // [BV] pay-then-print step; the payment and Monday.com calls inside it are [CG]
  const [paymentError, setPaymentError] = useState("");  // [CG] on-page, not a pop-up
  const handlePrint = async () => {
    if (printPending.current) return;
    const error = renewalError(personal, license, insurance);
    if (error || !initials) { setPaymentError(error || "Go back to Review and enter your initials before paying."); return; }
    printPending.current = true;
    setPrinting(true);
    setPaymentError("");
    if (!printReference.current) printReference.current = transactionId();  // [CG] short enough to fit the receipt
    sessionStorage.setItem("kiosk-payment-reference", printReference.current);
    let confirmed = paid;
    try {
      const payload = { registrant: `${personal.firstName} ${personal.lastName}`,
        license_plate: license.licensePlate, vehicle_year: license.vehicleYear,
        vehicle_make: license.vehicleMake, vehicle_model: license.vehicleModel,
        vin: license.vin, confirmation_number: printReference.current,
        receipt_email: personal.email.trim(),  // Stripe emails its receipt to this when the payment is captured
        security_event_id: crypto.randomUUID() };  // [CG] new one per Pay attempt so each attempt gets its own security photo
      if (!paid && !cardEntry.current) throw new Error("Wait for the card reader to connect.");
      confirmed = paid || await cardEntry.current!.pay(payload, setPaymentStatus);
      if (confirmed.status !== "succeeded") throw new Error("Payment is not confirmed. Retry to check its status.");
      setPaid(confirmed);
      setPaymentStatus("Payment approved. Sending to the printer…");
      const { address, city, state, zip, county, phone, email } = personal;
      setPrintResult(await submitPrint({
        ...payload,
        form_details: JSON.stringify({ personal: { address, city, state, zip, county, phone, email } }),
        initials,
      }));
      setSubmitted(true);

      // log the finished renewal to monday.com. runs in the background so the customer never waits on it
      if (!crmSavedRef.current) void saveToCrm(buildCrmPayload(payload.confirmation_number, confirmed.id));
    } catch (error) {
      const message = error instanceof Error ? error.message : "Payment or printing failed. Your form is still available.";
      setPaymentStatus("");
      setPaymentError((confirmed?.status === "succeeded" ? "Your payment went through, so do not pay again. " : "") + message);
    } finally {
      printPending.current = false;
      setPrinting(false);
    }
  };

  const stepTitles: Record<Step, string> = {  // [CG] steps 1 and 2 renamed
    1: "Scan Your Driver's License",
    2: "Driver's License Details",
    3: "Insurance & Vehicle Details",
    4: "Review & Confirm",
    5: "Payment",
  };

  // [CG] reworded so each screen says what to do, in order
  const stepSubtitles: Record<Step, string> = {
    1: "Scan your driver’s license, then type your email and phone number.",
    2: "Check that these match your driver’s license. Tap a field to fix it.",
    3: "Scan your insurance card, then check your vehicle and insurance details.",
    4: "Check everything one last time, then sign with your initials at the bottom.",
    5: "Pay $114.25 on the card reader. Your sticker and receipt print right after.",
  };

  // [CG] payment kicks off on its own once the reader connects. only once per visit, so a decline waits for Try Again
  const autoPaid = useRef(false);
  useEffect(() => {
    if (step !== 5) { autoPaid.current = false; return; }
    if (paymentReady && !paid && !printing && !autoPaid.current) {
      autoPaid.current = true;
      void handlePrint();
    }
  });  // [CG] checks every render, fires once

  if (checkingSession) return <p role="status">Opening kiosk…</p>;
  if (!authed) return <AdminLogin onLogin={() => setAuthed(true)} />;

  if (showingPrint) {
    const confNum = printReference.current;
    const printComplete = printResult?.state === "completed";
    const printStopped = printTimedOut || ["aborted", "cancelled", "unknown", "failed", "uncertain"].includes(printResult?.state ?? "");
    const emailState = printResult?.email_state;
    return (
      <div className="kiosk-ui min-h-screen flex flex-col" style={{ backgroundColor: "var(--background)" }}>
        {/* Header */}
        <header style={{ backgroundColor: "#ffffff", borderBottom: "3px solid var(--accent)" }}>
          <div className="max-w-6xl mx-auto px-6 py-3 flex items-center justify-between">
            <img src={logoUrl} alt="Registration Nation" className="h-12 w-auto object-contain" />
            <div className="text-right">
              <p className="text-xs font-semibold tracking-wider uppercase" style={{ color: "var(--muted-foreground)" }}>Texas Tag & Title</p>
              <p className="text-sm font-medium" style={{ color: "var(--primary)" }}>Vehicle Registration Renewal</p>
            </div>
          </div>
        </header>

        <div className="flex-1 flex items-center justify-center px-6 py-12">
          <div className="max-w-lg w-full flex flex-col items-center gap-8">

            {/* Printing graphic */}
            <div className="relative flex flex-col items-center h-40 shrink-0">
              <style>{`
                @keyframes printSlide { 0%{transform:translateY(-8px);opacity:0} 60%{opacity:1} 100%{transform:translateY(0);opacity:1} }
                @keyframes pulse-ring { 0%,100%{opacity:0.12;transform:translate(-50%,-50%) scale(1)} 50%{opacity:0.06;transform:translate(-50%,-50%) scale(1.15)} }
              `}</style>

              {/* Printer body */}
              <div className="relative z-10">
                <svg width="120" height="90" viewBox="0 0 120 90" fill="none">
                  {/* Body */}
                  <rect x="8" y="28" width="104" height="48" rx="8" fill="var(--primary)" />
                  {/* Paper tray top */}
                  <rect x="28" y="16" width="64" height="20" rx="4" fill="var(--secondary)" stroke="var(--border)" strokeWidth="1.5" />
                  {/* Slot */}
                  <rect x="24" y="58" width="72" height="6" rx="3" fill="rgba(255,255,255,0.15)" />
                  {/* Lights */}
                  <circle cx="90" cy="44" r="4" fill="var(--accent)" />
                  <circle cx="78" cy="44" r="4" fill="rgba(255,255,255,0.2)" />
                  {/* Legs */}
                  <rect x="22" y="74" width="16" height="10" rx="3" fill="var(--primary)" opacity="0.7" />
                  <rect x="82" y="74" width="16" height="10" rx="3" fill="var(--primary)" opacity="0.7" />
                </svg>

                {/* Sticker coming out */}
                <div className="absolute left-1/2 -translate-x-1/2" style={{ top: "56px", animation: "printSlide 0.8s ease 0.3s both" }}>
                  <div className="rounded-md overflow-hidden shadow-lg" style={{ width: "80px", border: "2px solid var(--accent)", backgroundColor: "#fff" }}>
                    <div className="py-1.5 px-2 flex flex-col items-center gap-0.5" style={{ backgroundColor: "var(--accent)" }}>
                      <p className="text-white font-bold text-center leading-tight" style={{ fontSize: "6px", letterSpacing: "0.05em" }}>TEXAS REGISTRATION</p>
                    </div>
                    <div className="p-1.5 flex flex-col gap-0.5">
                      <p className="font-bold text-center" style={{ fontSize: "8px", color: "var(--primary)" }}>{license.licensePlate || "KGP-4821"}</p>
                      <p className="text-center" style={{ fontSize: "6px", color: "var(--muted-foreground)" }}>{license.vehicleYear} {license.vehicleMake}</p>
                      <div className="mt-0.5 rounded text-center py-0.5" style={{ backgroundColor: "var(--primary)", fontSize: "5px", color: "#fff", letterSpacing: "0.1em" }}>
                        {new Date().getFullYear() + 1}
                      </div>
                    </div>
                  </div>
                </div>

                {/* Receipt coming out (offset) */}
                <div className="absolute left-1/2" style={{ top: "62px", marginLeft: "22px", animation: "printSlide 0.8s ease 0.7s both" }}>
                  <div className="rounded shadow-md" style={{ width: "54px", backgroundColor: "#fff", border: "1px solid var(--border)" }}>
                    <div className="p-1.5 flex flex-col gap-0.5">
                      <p className="font-bold" style={{ fontSize: "5px", color: "var(--primary)", letterSpacing: "0.08em" }}>RECEIPT</p>
                      {[`${personal.firstName} ${personal.lastName}`, license.licensePlate, "$114.25"].map((line, i) => (
                        <div key={i} className="h-px w-full rounded" style={{ backgroundColor: i === 2 ? "var(--accent)" : "var(--border)", marginTop: i === 2 ? "3px" : undefined }} />
                      ))}
                      <p style={{ fontSize: "5px", color: "var(--muted-foreground)" }}>DEMO · {new Date().toLocaleDateString()}</p>
                    </div>
                  </div>
                </div>
              </div>

              {/* Glow ring */}
              <div className="absolute rounded-full" style={{ width: 160, height: 160, top: "50%", left: "50%", transform: "translate(-50%,-50%)", backgroundColor: "var(--accent)", opacity: 0.08, animation: "pulse-ring 2s ease-in-out infinite" }} />
            </div>

            {/* Status text */}
            <div className="text-center flex flex-col gap-2">
              <h2 className="text-3xl" role="status">
                {printComplete ? "Collect your sticker and receipt" : printStopped ? "Ask a staff member for help" : "Printing your sticker and receipt…"}
              </h2>
              <p className="text-sm">
                {printComplete ? "Take both pages from the printer." : printStopped ? "Your payment went through. Please do not pay again." : "Your payment went through. Please wait by the printer."}
              </p>
              {printNotice && <p role="status">{printNotice}</p>}
              {printComplete && <p className="text-sm" role="status">
                {emailState === "sent" ? `Company receipt sent to ${personal.email}.`
                  : ["failed", "uncertain"].includes(emailState ?? "") ? "The email needs staff attention. Your printed receipt is ready."
                  : emailState === "skipped" ? "Your printed receipt is ready."
                  : "Preparing your receipt email…"}
              </p>}
            </div>

            {/* Summary card */}
            <div className="w-full rounded-xl overflow-hidden" style={{ border: "1px solid var(--border)", backgroundColor: "var(--card)" }}>
              <div className="px-5 py-3" style={{ backgroundColor: "var(--secondary)" }}>
                <p className="text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--primary)" }}>Transaction Summary</p>
              </div>
              <div className="px-5 py-1">
                {[
                  { label: "Registrant", value: `${personal.firstName} ${personal.lastName}` },
                  { label: "Vehicle", value: `${license.vehicleYear} ${license.vehicleMake} ${license.vehicleModel}` },
                  { label: "Plate", value: license.licensePlate },
                  { label: "Amount Paid", value: "$114.25" },
                  { label: "Payment method", value: "Card reader" },
                  { label: "Transaction ID", value: confNum },
                ].map((r) => (
                  <div key={r.label} className="flex justify-between items-center py-2.5" style={{ borderBottom: "1px solid var(--border)" }}>
                    <span className="text-sm" style={{ color: "var(--muted-foreground)" }}>{r.label}</span>
                    <span className="text-sm font-semibold" style={{ color: r.label === "Amount Paid" ? "#3a7d44" : "var(--foreground)" }}>{r.value || "—"}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* [CG] Monday (CRM) status removed per sponsor. failed saves still go to runtime/kiosk-problems.log */}

            {(printComplete || printStopped) && <CompletionTimer onReset={() => { setSubmitted(false); setPaid(null); setInitials(""); setPrintResult(null); setPrintNotice(""); setPrintTimedOut(false); setScores({}); setPaymentError(""); setPaymentStatus(""); sessionStorage.removeItem("kiosk-payment-reference"); printReference.current = ""; setStep(1); setLicensePrefilled(false); setPersonal({ firstName: "", lastName: "", dateOfBirth: "", email: "", phone: "", address: "", city: "", state: "", zip: "", county: "" }); setLicense({ licenseNumber: "", licenseState: "", expirationDate: "", licenseClass: "", vehicleYear: "", vehicleMake: "", vehicleModel: "", vin: "", licensePlate: "" }); setInsurance({ provider: "", policyNumber: "", policyHolder: "", startDate: "", endDate: "", coverageType: "", agentName: "", agentPhone: "" }); setPaymentReady(false); cardEntry.current = null; setCrmStatus("idle"); setCrmMessage(""); crmSavedRef.current = false; crmPayloadRef.current = null; }} />}
          </div>
        </div>
      </div>
    );
  }

  // [CG] Continue checks this step first. stops at the first blank, outlines it red and scrolls to it
  const goNext = () => {
    const current = step as FormStep;
    const error = stepError(current, personal, license, insurance);
    if (error) {
      const blank = firstBlank(current, personal, license, insurance);
      setFormError(error);
      setMissing(blank);
      requestAnimationFrame(() => document.querySelector(`[data-field="${blank}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" }));
      return;
    }
    setStep((s) => (s + 1) as Step);
  };

  return (
    <ScanConfidence.Provider value={confidence}>
    <div className="kiosk-ui min-h-screen" style={{ backgroundColor: "var(--background)" }}>
      {/* [CG] staff bar with sign-out */}
      {staff && (
        <div className="px-6 py-2 flex flex-wrap items-center justify-end gap-4 text-sm" style={{ background: "var(--primary)", color: "white" }}>
          <span>{staff.username}{staff.mode === "master" ? " · Master administrator" : " · Staff"}</span>
          <button type="button" disabled={signingOut} onClick={() => void handleSignOut()} className="underline">
            {signingOut ? "Signing out…" : "Sign out"}
          </button>
          {staffMessage && <p role="alert">{staffMessage}</p>}
        </div>
      )}
      {/* Header */}
      <header style={{ backgroundColor: "#ffffff", borderBottom: "3px solid var(--accent)" }}>
        <div className="max-w-6xl mx-auto px-6 py-3 flex items-center justify-between">
          <img
            src={logoUrl}
            alt="Registration Nation"
            className="h-12 w-auto object-contain"
          />
          <div className="text-right">
            <p className="text-xs font-semibold tracking-wider uppercase" style={{ color: "var(--muted-foreground)" }}>Texas Tag & Title</p>
            <p className="text-sm font-medium" style={{ color: "var(--primary)" }}>Vehicle Registration Renewal</p>
          </div>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-6 py-10">
        <ProgressBar current={step} />

        <div className="mb-8">
          <h1 className="text-3xl leading-tight mb-1">{stepTitles[step]}</h1>
          <p className="text-base" style={{ color: "var(--muted-foreground)" }}>{stepSubtitles[step]}</p>
        </div>

        <div
          className="rounded-xl p-7 mb-8"
          style={{ backgroundColor: "var(--card)", border: "1px solid var(--border)", boxShadow: "0 1px 4px rgba(15,25,35,0.06)" }}
        >
          {step === 1 && <Step1 data={personal} onChange={setPersonal} onLicenseScan={handleLicenseScan} licenseScanned={licensePrefilled} />}
          {step === 2 && <Step2 data={license} onChange={setLicense} />}
          {step === 3 && <Step3 data={insurance} onChange={setInsurance} vehicleData={license} onVehicleChange={setLicense} />}
          {step === 4 && <Step4 personal={personal} license={license} insurance={insurance} onPersonalChange={setPersonal} onLicenseChange={setLicense} onInsuranceChange={setInsurance} />}
          {step === 4 && <section className="mt-8 border-t pt-6 space-y-3">
            <h2 className="text-xl">Customer signature</h2>
            <p>By signing I confirm that all information with this transaction is true and correct. I understand, I have until 12pm the next business day to make any corrections at no charge. After 12pm, I will be responsible for all applicable charges for any changes. I also understand that if this transaction is rejected by the county for any reason, a rejection fee may apply. I will be notified of the rejection and given the opportunity to resolve any issues; however, any fees assessed as a result of the rejection are my responsibility.</p>
            <p>I also understand that this information is used to ensure your transaction goes well. We will use this information to contact you if any issues arise before the transaction is submitted to the State of Texas. We may use this information to remind you of your annual vehicle registration.</p>
            <label className="block max-w-xs">
              Enter your initials to sign
              <InputEl value={initials} autoCapitalize="characters" maxLength={8} required
                onChange={event => setInitials(event.target.value.replace(/[^a-z]/gi, "").toUpperCase())} />
            </label>
          </section>}
        {step === 5 && <TerminalPayment controllerRef={cardEntry} onReady={setPaymentReady} status={paymentStatus} paid={!!paid} busy={printing} error={paymentError} />}
        </div>

        {formError && (
          <div role="alert" className="rounded-lg px-4 py-3 mb-4 text-base font-semibold" style={{ backgroundColor: "#fdecea", border: "1px solid #f5c2bd", color: "#b42318" }}>
            {formError}
          </div>
        )}

        <div className="flex items-center justify-between">
          {step > 1 ? (
            <button
              onClick={() => setStep((s) => (s - 1) as Step)}
              disabled={printing || !!paid}
              className="flex items-center gap-2 text-sm font-medium px-4 py-2.5 rounded-md transition-all duration-150"
              style={{ color: "var(--primary)", border: "1.5px solid var(--border)", backgroundColor: "var(--card)" }}
              onMouseEnter={(e) => { e.currentTarget.style.borderColor = "var(--primary)"; }}
              onMouseLeave={(e) => { e.currentTarget.style.borderColor = "var(--border)"; }}
            >
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <path d="M9 2L4 7l5 5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              Back
            </button>
          ) : <div />}

          {step < 4 ? (
            <button
              onClick={goNext}
              className="flex items-center gap-2 text-sm font-semibold px-6 py-2.5 rounded-md transition-all duration-150"
              style={{ backgroundColor: "var(--primary)", color: "var(--primary-foreground)" }}
              onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = "#142e4a"; }}
              onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = "var(--primary)"; }}
            >
              Continue
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <path d="M5 2l5 5-5 5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          ) : step === 4 ? (
            <button
              onClick={() => {
                const error = renewalError(personal, license, insurance);
                if (error || !initials) { setFormError(error || "Enter your initials in the signature box before continuing."); return; }  // [CG] was a pop-up
                setStep(5);
              }}
              className="flex items-center gap-2 text-sm font-semibold px-6 py-2.5 rounded-md transition-all duration-150"
              style={{ backgroundColor: "var(--primary)", color: "var(--primary-foreground)" }}
              onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = "#142e4a"; }}
              onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = "var(--primary)"; }}
            >
              Proceed to Payment
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <path d="M5 2l5 5-5 5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          ) : (
            <button
              onClick={handlePrint}
              disabled={printing || (!paid && !paymentReady)}
              className="flex items-center gap-2 text-sm font-semibold px-6 py-2.5 rounded-md transition-all duration-150"
              style={{ backgroundColor: "#3a7d44", color: "#ffffff" }}
              onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = "#2d6636"; }}
              onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = "#3a7d44"; }}
            >
              {/* [CG] "Try Again" after a decline */}
              {printing ? (paid ? "Sending to printer…" : "Waiting for the card reader…") : paid ? "Retry Printing" : paymentError ? "Try Again" : "Pay $114.25"}
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <path d="M2 7h10M8 3l4 4-4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          )}
        </div>

        <p className="text-xs text-center mt-8" style={{ color: "var(--muted-foreground)" }}>
          Fields marked with <span style={{ color: "#e05a1a" }}>*</span> are required. 
        </p>
      </main>
    </div>
    </ScanConfidence.Provider>
  );
}
