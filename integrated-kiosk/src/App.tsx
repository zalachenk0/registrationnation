import { useState, useRef, useEffect } from "react";
import logoUrl from "./imports/RN_Logo.png";
import { fillBlank, requestScan, submitPrint, type ScanResult } from "./integration";
import { getPaymentConfig, PAYMENT_AMOUNT, type CardEntry, type PaymentResult } from "./payment";
import { CardPayment } from "./CardPayment";
import { TerminalPayment } from "./TerminalPayment";
import { createMondayRecord } from "./monday";
import { emailReceipt } from "./receiptEmail";
import { CompletionTimer } from "./CompletionTimer";
import { renewalError } from "./renewal";

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

const STEPS = [
  { number: 1, label: "Personal Info" },
  { number: 2, label: "Driver's License" },
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
            width: `${((current - 1) / 3) * 100}%`,
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

function Field({
  label,
  required,
  children,
  hint,
}: {
  label: string;
  required?: boolean;
  children: React.ReactNode;
  hint?: string;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-sm font-medium" style={{ color: "var(--foreground)" }}>
        {label}
        {required && <span style={{ color: "#e05a1a" }} className="ml-0.5">*</span>}
      </label>
      {children}
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

function InputEl(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={inputClass}
      style={inputStyle}
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
  const set = (k: keyof PersonalInfo) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    onChange({ ...data, [k]: e.target.value });

  const handleScan = (result: ScanResult) => {
    setScanned(true);
    setExpanded(true);
    const f = result.fields;
    onChange(current => fillBlank(current, {
      firstName: f.customer_first_name, lastName: f.customer_last_name,
      dateOfBirth: f.date_of_birth, address: f.street_address,
      city: f.city, state: stateName(f.state), zip: f.zip_code,
    }));
    onLicenseScan(result);
  };

  return (
    <div className="flex flex-col gap-6">
      <ScanPrompt
        title="Scan Your Driver's License"
        subtitle="We'll extract your name, address, and date of birth automatically. You'll just need to add your email and phone."
        docType="license"
        scanned={scanned}
        onScan={handleScan}
        onManual={() => setExpanded(true)}
      />

      {/* Contact — always manual */}
      <div className="flex flex-col gap-5">
        <div className="flex items-center gap-3">
          <div className="h-px flex-1" style={{ backgroundColor: "var(--border)" }} />
          <span className="text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--muted-foreground)" }}>Enter manually</span>
          <div className="h-px flex-1" style={{ backgroundColor: "var(--border)" }} />
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
          <Field label="Email Address" required>
            <InputEl type="email" placeholder="margaret.sullivan@email.com" value={data.email} onChange={set("email")} />
          </Field>
          <Field label="Phone Number" required>
            <InputEl type="tel" placeholder="(555) 867-5309" value={data.phone} onChange={set("phone")} />
          </Field>
        </div>
      </div>

      {/* Personal fields — pre-filled by scan, editable */}
      {expanded && (
        <div className="flex flex-col gap-5">
          <div className="flex items-center gap-3">
            <div className="h-px flex-1" style={{ backgroundColor: "var(--border)" }} />
            <span className="text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--muted-foreground)" }}>From license — review & edit</span>
            <div className="h-px flex-1" style={{ backgroundColor: "var(--border)" }} />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
            <Field label="First Name" required>
              <InputEl placeholder="Margaret" value={data.firstName} onChange={set("firstName")} />
            </Field>
            <Field label="Last Name" required>
              <InputEl placeholder="Sullivan" value={data.lastName} onChange={set("lastName")} />
            </Field>
          </div>
          <Field label="Date of Birth" required>
            <InputEl type="date" value={data.dateOfBirth} onChange={set("dateOfBirth")} />
          </Field>
          <Field label="Street Address" required>
            <InputEl placeholder="742 Evergreen Terrace" value={data.address} onChange={set("address")} />
          </Field>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-5">
            <Field label="City" required>
              <InputEl placeholder="Austin" value={data.city} onChange={set("city")} />
            </Field>
            <Field label="State" required>
              <SelectEl value={data.state} onChange={set("state")}>
                {data.state && !US_STATES.includes(data.state) && <option>{data.state}</option>}
                <option value="">Select</option>
                {US_STATES.map((s) => <option key={s}>{s}</option>)}
              </SelectEl>
            </Field>
            <Field label="ZIP Code" required>
              <InputEl placeholder="78701" value={data.zip} onChange={set("zip")} maxLength={10} />
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
        const response = await fetch("/api/camera/frame.jpg", { cache: "no-store", signal: controller.signal });
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

  const handleCapture = () => { if (frameUrl && !camError) onCapture(); };
  const handleClose = onClose;

  const [insuranceFormat, setInsuranceFormat] = useState<"paper" | "phone">("paper");
  const shape = docType === "license" ? "license" : insuranceFormat;
  const guides = {
    license: {
      title: "Position Your Driver’s License",
      subtitle: "Place the back of your license under the camera, long edge horizontal.",
      position: "Landscape · Back side", label: "Keep your whole license visible",
      detail: "Keep the full barcode visible",
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
          <button onClick={handleCapture} disabled={!frameUrl || Boolean(camError)}
            className="flex-[2] py-3 rounded-lg text-sm font-semibold disabled:opacity-40 flex items-center justify-center gap-2"
            style={{ backgroundColor: "var(--accent)", color: "#fff" }}>
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
              <circle cx="9" cy="9" r="6" stroke="currentColor" strokeWidth="1.8" />
              <circle cx="9" cy="9" r="3" fill="currentColor" />
            </svg>
            Capture &amp; Scan
          </button>
        </div>
        <p className="text-xs text-center" style={{ color: "#aab8c7" }}>Hold still, then select Capture &amp; Scan.</p>
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
}: {
  title: string;
  subtitle: string;
  docType: "license" | "insurance";
  scanned: boolean;
  onScan: (result: ScanResult) => void;
  onManual?: () => void;
}) {
  const [state, setState] = useState<ScanState>(scanned ? "done" : "idle");
  const [cameraOpen, setCameraOpen] = useState(false);

  const [error, setError] = useState("");
  const [recommendation, setRecommendation] = useState("");
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
      setRecommendation(result.recommendation || "");
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
        {state === "idle" && (
          <div className="flex flex-col items-center gap-4 px-6 py-8 text-center">
            <div className="w-16 h-16 rounded-2xl flex items-center justify-center" style={{ backgroundColor: "var(--secondary)" }}>
              <svg width="32" height="32" viewBox="0 0 24 24" fill="none" style={{ color: "var(--primary)" }}>
                <rect x="2" y="6" width="20" height="14" rx="2" stroke="currentColor" strokeWidth="1.5" />
                <circle cx="12" cy="13" r="3.5" stroke="currentColor" strokeWidth="1.5" />
                <path d="M8 6l1.2-2.4A1 1 0 0110.1 3h3.8a1 1 0 01.9.6L16 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                <circle cx="18.5" cy="9.5" r="1" fill="currentColor" />
              </svg>
            </div>
            <div>
              <p className="font-semibold text-sm mb-1" style={{ color: "var(--foreground)" }}>{title}</p>
              <p className="text-xs leading-relaxed" style={{ color: "var(--muted-foreground)" }} role={error ? "alert" : undefined}>{error || subtitle}</p>
            </div>
            <button
              onClick={() => setCameraOpen(true)}
              className="flex items-center justify-center gap-2 px-8 py-3 rounded-lg text-sm font-semibold transition-all duration-150"
              style={{ backgroundColor: "var(--accent)", color: "#ffffff" }}
              onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = "#d95c1a"; }}
              onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = "var(--accent)"; }}
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                <rect x="1" y="3" width="14" height="10" rx="1.5" stroke="currentColor" strokeWidth="1.4" />
                <circle cx="8" cy="8" r="2.5" stroke="currentColor" strokeWidth="1.4" />
                <circle cx="12.5" cy="5.5" r="1" fill="currentColor" />
              </svg>
              Open Scanner
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
              <span className="text-sm font-medium" style={{ color: "var(--primary)" }}>Reading document…</span>
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
              <p className="text-sm font-semibold" style={{ color: "#2d6636" }}>Document scanned successfully</p>
              <p className="text-xs truncate" style={{ color: "#3a7d44" }} title={recommendation || undefined}>{recommendation || "Fields below have been pre-filled — review and correct as needed."}</p>
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
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
          <Field label="License Number" required>
            <InputEl placeholder="D123-4567-8901" value={data.licenseNumber} onChange={set("licenseNumber")} />
          </Field>
          <Field label="Issuing State" required>
            <SelectEl value={data.licenseState} onChange={set("licenseState")}>
                {data.licenseState && !US_STATES.includes(data.licenseState) && <option>{data.licenseState}</option>}
              <option value="">Select</option>
              {US_STATES.map((s) => <option key={s}>{s}</option>)}
            </SelectEl>
          </Field>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
          <Field label="Expiration Date" required>
            <InputEl type="date" value={data.expirationDate} onChange={set("expirationDate")} />
          </Field>
          <Field label="License Class" required>
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

  const handleInsuranceScan = (result: ScanResult) => {
    setInsuranceScanned(true);
    const f = result.fields;
    onChange(current => fillBlank(current, {
      provider: f.insurance_company, policyNumber: f.policy_number, endDate: f.insurance_expiration,
    }));
    onVehicleChange(current => fillBlank(current, {
      vehicleYear: f.vehicle_year, vehicleMake: f.vehicle_make, vehicleModel: f.vehicle_model,
      vin: f.vehicle_vin?.length === 17 ? f.vehicle_vin : undefined,
    }));
  };

  return (
    <div className="flex flex-col gap-6">
      <ScanPrompt
        title="Scan Proof of Insurance"
        subtitle="Upload your insurance card or declaration page to auto-fill your policy and vehicle details below."
        docType="insurance"
        scanned={insuranceScanned}
        onScan={handleInsuranceScan}
      />
      <div
        className="rounded-lg p-5 flex flex-col gap-5"
        style={{ border: "1px solid var(--border)", backgroundColor: "var(--card)" }}
      >
        <p className="text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--muted-foreground)" }}>Vehicle Information</p>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-5">
          <Field label="Year" required>
            <InputEl placeholder="2019" value={vehicleData.vehicleYear} onChange={setVehicle("vehicleYear")} maxLength={4} />
          </Field>
          <Field label="Make" required>
            <InputEl placeholder="Toyota" value={vehicleData.vehicleMake} onChange={setVehicle("vehicleMake")} />
          </Field>
          <Field label="Model" required>
            <InputEl placeholder="Camry" value={vehicleData.vehicleModel} onChange={setVehicle("vehicleModel")} />
          </Field>
        </div>
        <Field label="VIN" required hint="17-character Vehicle Identification Number">
          <InputEl placeholder="1HGBH41JXMN109186" value={vehicleData.vin} onChange={setVehicle("vin")} maxLength={17} />
        </Field>
        <Field label="Current License Plate" required>
          <InputEl placeholder="ABC-1234" value={vehicleData.licensePlate} onChange={setVehicle("licensePlate")} />
        </Field>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
        <Field label="Insurance Provider" required>
          <SelectEl value={data.provider} onChange={set("provider")}>
            <option value="">Select provider</option>
            {data.provider && !["State Farm", "Geico", "Progressive", "Allstate", "USAA", "Liberty Mutual", "Farmers", "Nationwide", "Travelers", "Other"].includes(data.provider) && <option>{data.provider}</option>}
            {["State Farm", "Geico", "Progressive", "Allstate", "USAA", "Liberty Mutual", "Nationwide", "Farmers", "Travelers", "Other"].map((p) => (
              <option key={p}>{p}</option>
            ))}
          </SelectEl>
        </Field>
        <Field label="Policy Number" required>
          <InputEl placeholder="POL-8821049-A" value={data.policyNumber} onChange={set("policyNumber")} />
        </Field>
      </div>
      <Field label="Policy Holder Name" required>
        <InputEl placeholder="Margaret Sullivan" value={data.policyHolder} onChange={set("policyHolder")} />
      </Field>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
        <Field label="Coverage Start Date (optional)">
          <InputEl type="date" value={data.startDate} onChange={set("startDate")} />
        </Field>
        <Field label="Policy End Date" required>
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
            <InputEl placeholder="Robert Chen" value={data.agentName} onChange={set("agentName")} />
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
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  type?: string;
  options?: string[];
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
              <input
                autoFocus
                type={type}
                value={value}
                onChange={(e) => onChange(e.target.value)}
                onBlur={() => setEditing(false)}
                onKeyDown={(e) => e.key === "Enter" && setEditing(false)}
                className="text-sm rounded px-2 py-1 outline-none text-right"
                style={{ border: "1.5px solid var(--accent)", backgroundColor: "var(--card)", color: "var(--foreground)", fontFamily: "'Instrument Sans', sans-serif", minWidth: 0, maxWidth: 200 }}
              />
            )}
            <button
              onMouseDown={() => setEditing(false)}
              className="shrink-0 text-xs font-semibold px-2 py-1 rounded transition-colors"
              style={{ backgroundColor: "var(--accent)", color: "#fff" }}
            >
              Done
            </button>
          </>
        ) : (
          <>
            <span className="text-sm font-medium text-right truncate" style={{ color: value ? "var(--foreground)" : "var(--muted-foreground)" }}>
              {value || "—"}
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
        <EditableRow label="First Name"    value={personal.firstName}   onChange={sp("firstName")} />
        <EditableRow label="Last Name"     value={personal.lastName}    onChange={sp("lastName")} />
        <EditableRow label="Date of Birth" value={personal.dateOfBirth} onChange={sp("dateOfBirth")} type="date" />
        <EditableRow label="Email"         value={personal.email}       onChange={sp("email")} type="email" />
        <EditableRow label="Phone"         value={personal.phone}       onChange={sp("phone")} type="tel" />
        <EditableRow label="Address"       value={personal.address}     onChange={sp("address")} />
        <EditableRow label="City"          value={personal.city}        onChange={sp("city")} />
        <EditableRow label="State"         value={personal.state}       onChange={sp("state")} options={US_STATES} />
        <EditableRow label="ZIP"           value={personal.zip}         onChange={sp("zip")} />
      </EditableSection>

      <EditableSection title="Driver's License">
        <EditableRow label="License Number" value={license.licenseNumber}  onChange={sl("licenseNumber")} />
        <EditableRow label="Issuing State"  value={license.licenseState}   onChange={sl("licenseState")} options={US_STATES} />
        <EditableRow label="Expiration"     value={license.expirationDate} onChange={sl("expirationDate")} type="date" />
        <EditableRow label="Class"          value={license.licenseClass}   onChange={sl("licenseClass")} options={["A","B","C","D","M"]} />
        <EditableRow label="Year"           value={license.vehicleYear}    onChange={sl("vehicleYear")} />
        <EditableRow label="Make"           value={license.vehicleMake}    onChange={sl("vehicleMake")} />
        <EditableRow label="Model"          value={license.vehicleModel}   onChange={sl("vehicleModel")} />
        <EditableRow label="VIN"            value={license.vin}            onChange={sl("vin")} />
        <EditableRow label="License Plate"  value={license.licensePlate}   onChange={sl("licensePlate")} />
      </EditableSection>

      <EditableSection title="Insurance">
        <EditableRow label="Provider"      value={insurance.provider}     onChange={si("provider")} />
        <EditableRow label="Policy Number" value={insurance.policyNumber} onChange={si("policyNumber")} />
        <EditableRow label="Policy Holder" value={insurance.policyHolder} onChange={si("policyHolder")} />
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

  const attempt = () => {
    if (username === "blastervee" && password === "Nikolai01") {
      onLogin();
    } else {
      setError(true);
      setShaking(true);
      setTimeout(() => setShaking(false), 500);
    }
  };

  return (
    <div className="min-h-screen flex flex-col items-center justify-center px-4" style={{ backgroundColor: "var(--primary)" }}>
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

  const cardEntry = useRef<CardEntry | null>(null);
  const [paymentReady, setPaymentReady] = useState(false);

  // payment method: the S700 card reader is the primary option and the typed card is the backup.
  // null = still asking the server which methods are set up
  const [paymentMode, setPaymentMode] = useState<"terminal" | "card" | null>(null);
  const [readerAvailable, setReaderAvailable] = useState(false);
  const [readerProblem, setReaderProblem] = useState("");
  useEffect(() => {
    let active = true;
    getPaymentConfig().then(config => {
      if (!active) return;
      setReaderAvailable(!!config.terminal?.enabled);
      setReaderProblem(config.terminal?.problem || "");
      setPaymentMode(current => current ?? (config.terminal?.enabled ? "terminal" : "card"));
    }).catch(() => { if (active) setPaymentMode(current => current ?? "card"); });  // the card form shows the real error
    return () => { active = false; };
  }, []);

  // monday.com crm record, shown on the success screen
  const [crmStatus, setCrmStatus] = useState<"idle" | "saving" | "saved" | "failed">("idle");
  const [crmMessage, setCrmMessage] = useState("");
  const crmSavedRef = useRef(false);  // so a print retry can never make a second crm record
  const crmPayloadRef = useRef<{ itemName: string; fields: Record<string, string | number> } | null>(null);  // kept so the retry button can resend it

  // the texas tag & title receipt pdf we email to the customer, shown on the success screen next to the crm row
  const [emailStatus, setEmailStatus] = useState<"idle" | "sending" | "sent" | "failed">("idle");
  const [emailMessage, setEmailMessage] = useState("");
  const emailSentRef = useRef(false);   // so a print retry can't start a second email (the server also sends only one per payment)
  const emailBusyRef = useRef(false);   // so a double click on retry can't run two at once
  const emailPayloadRef = useRef<Record<string, string> | null>(null);  // kept so the retry button can resend it

  const [personal, setPersonal] = useState<PersonalInfo>({
    firstName: "", lastName: "", dateOfBirth: "", email: "", phone: "",
    address: "", city: "", state: "", zip: "",
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
      expirationDate: f.license_expiration, licenseClass: f.license_class,
    }));
  };
  const [insurance, setInsurance] = useState<InsuranceInfo>({
    provider: "", policyNumber: "", policyHolder: "", startDate: "",
    endDate: "", coverageType: "", agentName: "", agentPhone: "",
  });

  const [printing, setPrinting] = useState(false);
  const [paid, setPaid] = useState<PaymentResult | null>(null);
  const [paymentStatus, setPaymentStatus] = useState("");
  const printPending = useRef(false);
  const printReference = useRef(sessionStorage.getItem("kiosk-payment-reference") || "");
  // builds what we send to monday.com. the server only forwards the fields listed in COLUMN_MAP (monday_service.py)
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

  const handlePrint = async () => {
    if (printPending.current) return;
    const error = renewalError(personal, license, insurance);
    if (error) { window.alert(error); return; }
    printPending.current = true;
    setPrinting(true);
    if (!printReference.current) printReference.current = `REG-${crypto.randomUUID().toUpperCase()}`;
    sessionStorage.setItem("kiosk-payment-reference", printReference.current);
    let confirmed = paid;
    try {
      const payload = { registrant: `${personal.firstName} ${personal.lastName}`,
        license_plate: license.licensePlate, vehicle_year: license.vehicleYear,
        vehicle_make: license.vehicleMake, vehicle_model: license.vehicleModel,
        vin: license.vin, confirmation_number: printReference.current,
        receipt_email: personal.email.trim() };  // Stripe emails its receipt to this when the payment is captured
      if (!paid && !cardEntry.current) throw new Error("Wait for the card reader or card form to finish loading, then retry.");
      confirmed = paid || await cardEntry.current!.pay(payload, setPaymentStatus);
      if (confirmed.status !== "succeeded") throw new Error("Payment is not confirmed. Retry to check its status.");
      setPaid(confirmed);
      setPaymentStatus("Test payment confirmed. Sending to printer…");
      void sendReceiptEmail(payload);   // emailed as soon as the payment is confirmed, so it goes out even if the printer is down
      await submitPrint(payload);
      setSubmitted(true);

      // log the finished renewal to monday.com. runs in the background so the customer never waits on it
      if (!crmSavedRef.current) void saveToCrm(buildCrmPayload(payload.confirmation_number, confirmed.id));
    } catch (error) {
      const message = error instanceof Error ? error.message : "Payment or printing failed. Your form is still available.";
      setPaymentStatus(confirmed ? "Test payment confirmed. Retry printing without paying again." : "Payment not confirmed. Retry to check the same transaction.");
      window.alert((confirmed ? "Test payment succeeded; do not pay again. " : "") + message);
    } finally {
      printPending.current = false;
      setPrinting(false);
    }
  };

  // emails the receipt pdf. runs in the background and never blocks payment or printing
  const sendReceiptEmail = async (payload: Record<string, string>) => {
    if (emailBusyRef.current || emailSentRef.current) return;
    emailBusyRef.current = true;
    emailPayloadRef.current = payload;
    setEmailStatus("sending");
    setEmailMessage("");
    const result = await emailReceipt(payload);
    emailBusyRef.current = false;
    if (result.success) {
      emailSentRef.current = true;
      setEmailStatus("sent");
      setEmailMessage(result.to);
    } else {
      setEmailStatus("failed");
      setEmailMessage(result.error);
    }
  };

  const stepTitles: Record<Step, string> = {
    1: "Personal Information",
    2: "Driver's License",
    3: "Insurance & Vehicle Details",
    4: "Review & Confirm",
    5: "Payment",
  };

  const stepSubtitles: Record<Step, string> = {
    1: "Scan your driver's license — we'll fill in the rest. Just add your email and phone.",
    2: "Confirm the driver's license details captured by the scanner.",
    3: "Scan your proof of insurance to fill in your policy and vehicle information.",
    4: "Review all information before proceeding to payment.",
    5: "Complete your $114.25 renewal payment securely.",
  };

  if (!authed) return <AdminLogin onLogin={() => setAuthed(true)} />;

  if (submitted) {
    const confNum = printReference.current;
    return (
      <div className="min-h-screen flex flex-col" style={{ backgroundColor: "var(--background)" }}>
        {/* Header */}
        <header style={{ backgroundColor: "#ffffff", borderBottom: "3px solid var(--accent)" }}>
          <div className="max-w-2xl mx-auto px-6 py-3 flex items-center justify-between">
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
              <h2 className="text-3xl" style={{ color: "var(--foreground)" }}>Payment Complete — Printing Queued</h2>
              <p className="text-sm" style={{ color: "var(--muted-foreground)" }}>
                Your $114.25 Stripe test payment succeeded and the mock sticker and receipt were accepted by the print queue. No real money was charged. Check the printer to collect them.
              </p>
              {paid?.receiptEmail ? (
                <p className="text-xs mt-1 flex items-center justify-center gap-1.5" style={{ color: "var(--muted-foreground)" }}>
                  <svg width="13" height="13" viewBox="0 0 13 13" fill="none" style={{ flexShrink: 0 }}>
                    <rect x="1" y="2.5" width="11" height="8" rx="1.5" stroke="currentColor" strokeWidth="1.2" />
                    <path d="M1.5 3.5l5 3.5 5-3.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                  Stripe will also email its own payment receipt to <strong className="ml-0.5">{paid.receiptEmail}</strong>
                </p>
              ) : personal.email ? (
                <p className="text-xs mt-1" style={{ color: "var(--muted-foreground)" }}>
                  No receipt email could be set up for <strong>{personal.email}</strong>. Check the address on the Review step.
                </p>
              ) : (
                <p className="text-xs mt-1" style={{ color: "var(--muted-foreground)" }}>
                  No email address was entered, so no receipt email is sent.
                </p>
              )}
              {paid?.receiptEmail && (
                <p className="text-xs" style={{ color: "var(--muted-foreground)" }}>
                  In Stripe test mode, receipts are only delivered to the Stripe account owner's own email address.
                </p>
              )}
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
                  { label: "Amount Paid", value: "$114.25 (Stripe test)" },
                  { label: "Stripe Payment", value: paid?.id || "—" },
                  { label: "Stripe receipt email", value: paid?.receiptEmail || "Not sent" },
                  { label: "Confirmation #", value: confNum },
                ].map((r) => (
                  <div key={r.label} className="flex justify-between items-center py-2.5" style={{ borderBottom: "1px solid var(--border)" }}>
                    <span className="text-sm" style={{ color: "var(--muted-foreground)" }}>{r.label}</span>
                    <span className="text-sm font-semibold" style={{ color: r.label === "Amount Paid" ? "#3a7d44" : "var(--foreground)" }}>{r.value || "—"}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* monday.com crm status */}
            {emailStatus !== "idle" && (
              <div className="w-full flex flex-col gap-2">
                <div className="flex justify-between items-center rounded-lg px-4 py-3 text-sm" style={{ border: "1px solid var(--border)", backgroundColor: "var(--card)" }}>
                  <span style={{ color: "var(--muted-foreground)" }}>Receipt PDF by email</span>
                  <span className="font-semibold" style={{ color: emailStatus === "sent" ? "#3a7d44" : emailStatus === "failed" ? "#b42318" : "var(--muted-foreground)" }}>
                    {emailStatus === "sending" ? "Sending..." : emailStatus === "sent" ? "Sent ✓" : "Not sent"}
                  </span>
                </div>
                {emailStatus === "sent" && <p className="text-xs px-1" style={{ color: "var(--muted-foreground)" }}>Emailed to {emailMessage}</p>}
                {emailStatus === "failed" && <p className="text-xs px-1" style={{ color: "#b42318" }}>{emailMessage}</p>}
                {emailStatus === "failed" && (
                  <button type="button" onClick={() => { if (emailPayloadRef.current) void sendReceiptEmail(emailPayloadRef.current); }} className="self-start px-4 py-2 rounded-md text-xs font-semibold" style={{ border: "1px solid var(--border)", backgroundColor: "var(--card)", color: "var(--primary)" }}>
                    Retry sending receipt
                  </button>
                )}
              </div>
            )}

            {crmStatus !== "idle" && (
              <div className="w-full flex flex-col gap-2">
                <div className="flex justify-between items-center rounded-lg px-4 py-3 text-sm" style={{ border: "1px solid var(--border)", backgroundColor: "var(--card)" }}>
                  <span style={{ color: "var(--muted-foreground)" }}>CRM record (Monday.com)</span>
                  <span className="font-semibold" style={{ color: crmStatus === "saved" ? "#3a7d44" : crmStatus === "failed" ? "#b42318" : "var(--muted-foreground)" }}>
                    {crmStatus === "saving" ? "Saving..." : crmStatus === "saved" ? "Saved ✓" : "Not saved"}
                  </span>
                </div>
                {crmMessage && <p className="text-xs px-1" style={{ color: crmStatus === "failed" ? "#b42318" : "var(--muted-foreground)" }}>{crmMessage}</p>}
                {crmStatus === "failed" && (
                  <button type="button" onClick={() => { if (crmPayloadRef.current) void saveToCrm(crmPayloadRef.current); }} className="self-start px-4 py-2 rounded-md text-xs font-semibold" style={{ border: "1px solid var(--border)", backgroundColor: "var(--card)", color: "var(--primary)" }}>
                    Retry saving to CRM
                  </button>
                )}
              </div>
            )}

            <CompletionTimer onReset={() => { setSubmitted(false); setPaid(null); setPaymentStatus(""); sessionStorage.removeItem("kiosk-payment-reference"); printReference.current = ""; setStep(1); setLicensePrefilled(false); setPersonal({ firstName: "", lastName: "", dateOfBirth: "", email: "", phone: "", address: "", city: "", state: "", zip: "" }); setLicense({ licenseNumber: "", licenseState: "", expirationDate: "", licenseClass: "", vehicleYear: "", vehicleMake: "", vehicleModel: "", vin: "", licensePlate: "" }); setInsurance({ provider: "", policyNumber: "", policyHolder: "", startDate: "", endDate: "", coverageType: "", agentName: "", agentPhone: "" }); setPaymentReady(false); cardEntry.current = null; setPaymentMode(readerAvailable ? "terminal" : "card"); setCrmStatus("idle"); setCrmMessage(""); crmSavedRef.current = false; crmPayloadRef.current = null; setEmailStatus("idle"); setEmailMessage(""); emailSentRef.current = false; emailBusyRef.current = false; emailPayloadRef.current = null; }} />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen" style={{ backgroundColor: "var(--background)" }}>
      {/* Header */}
      <header style={{ backgroundColor: "#ffffff", borderBottom: "3px solid var(--accent)" }}>
        <div className="max-w-2xl mx-auto px-6 py-3 flex items-center justify-between">
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

      <main className="max-w-2xl mx-auto px-6 py-10">
        <ProgressBar current={step} />

        <div className="mb-8">
          <h1 className="text-3xl leading-tight mb-1">{stepTitles[step]}</h1>
          <p className="text-sm" style={{ color: "var(--muted-foreground)" }}>{stepSubtitles[step]}</p>
        </div>

        <div
          className="rounded-xl p-7 mb-8"
          style={{ backgroundColor: "var(--card)", border: "1px solid var(--border)", boxShadow: "0 1px 4px rgba(15,25,35,0.06)" }}
        >
          {step === 1 && <Step1 data={personal} onChange={setPersonal} onLicenseScan={handleLicenseScan} licenseScanned={licensePrefilled} />}
          {step === 2 && <Step2 data={license} onChange={setLicense} />}
          {step === 3 && <Step3 data={insurance} onChange={setInsurance} vehicleData={license} onVehicleChange={setLicense} />}
          {step === 4 && <Step4 personal={personal} license={license} insurance={insurance} onPersonalChange={setPersonal} onLicenseChange={setLicense} onInsuranceChange={setInsurance} />}
          {step === 5 && paymentMode === null && <p role="status" className="text-sm">Checking payment options…</p>}
        {step === 5 && paymentMode === "terminal" && <TerminalPayment controllerRef={cardEntry} onReady={setPaymentReady} status={paymentStatus} paid={!!paid} busy={printing} onUseManual={() => { setPaymentStatus(""); setPaymentMode("card"); }} />}
        {step === 5 && paymentMode === "card" && <CardPayment controllerRef={cardEntry} onReady={setPaymentReady} status={paymentStatus} paid={!!paid} busy={printing} notice={readerProblem} onUseReader={readerAvailable ? () => { setPaymentStatus(""); setPaymentMode("terminal"); } : undefined} />}
        </div>

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
              onClick={() => setStep((s) => (s + 1) as Step)}
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
              onClick={() => setStep(5)}
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
              {printing ? (paid ? "Sending to printer…" : "Processing payment…") : paid ? "Retry Printing" : "Pay $114.25"}
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <path d="M2 7h10M8 3l4 4-4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          )}
        </div>

        <p className="text-xs text-center mt-8" style={{ color: "var(--muted-foreground)" }}>
          Fields marked with <span style={{ color: "#e05a1a" }}>*</span> are required. Document scanning runs locally; card details are collected securely by Stripe.
        </p>
      </main>
    </div>
  );
}
