import { useState, useRef, useEffect } from "react";
import { chargeCard, connectToReader } from "./payment";
import { createMondayRecord } from "./monday";

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

interface LicenseScanFields {
  customer_first_name?: string;
  customer_last_name?: string;
  date_of_birth?: string;
  street_address?: string;
  city?: string;
  state?: string;
  zip_code?: string;
  license_number?: string;
  license_expiration?: string;
  license_class?: string;
}

interface InsuranceScanFields {
  vehicle_year?: string;
  vehicle_make?: string;
  vehicle_model?: string;
  vehicle_vin?: string;
  insurance_expiration?: string;
}

interface ScanResponse<T> {
  success: boolean;
  recommendation: string;
  fields: T;
}

interface RegistrationPrintPayload {
  registrant: string;
  license_plate: string;
  vehicle_year: string;
  vehicle_make: string;
  vehicle_model: string;
  vin: string;
  amount: string;
  confirmation_number: string;
}

const STATE_NAMES: Record<string, string> = {
  AL: "Alabama", AK: "Alaska", AZ: "Arizona", AR: "Arkansas", CA: "California",
  CO: "Colorado", CT: "Connecticut", DE: "Delaware", FL: "Florida", GA: "Georgia",
  HI: "Hawaii", ID: "Idaho", IL: "Illinois", IN: "Indiana", IA: "Iowa", KS: "Kansas",
  KY: "Kentucky", LA: "Louisiana", ME: "Maine", MD: "Maryland", MA: "Massachusetts",
  MI: "Michigan", MN: "Minnesota", MS: "Mississippi", MO: "Missouri", MT: "Montana",
  NE: "Nebraska", NV: "Nevada", NH: "New Hampshire", NJ: "New Jersey", NM: "New Mexico",
  NY: "New York", NC: "North Carolina", ND: "North Dakota", OH: "Ohio", OK: "Oklahoma",
  OR: "Oregon", PA: "Pennsylvania", RI: "Rhode Island", SC: "South Carolina",
  SD: "South Dakota", TN: "Tennessee", TX: "Texas", UT: "Utah", VT: "Vermont",
  VA: "Virginia", WA: "Washington", WV: "West Virginia", WI: "Wisconsin", WY: "Wyoming",
};

async function scanDocument<T>(kind: "license" | "insurance", file: File): Promise<ScanResponse<T>> {
  const body = new FormData();
  body.append("image", file);
  const response = await fetch(`/api/scan/${kind}`, { method: "POST", body });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `Scanner service returned ${response.status}.`);
  }
  if (!payload.success) {
    throw new Error(payload.recommendation || "The document could not be read. Please retake the photo.");
  }
  return payload as ScanResponse<T>;
}

async function scanPiCamera<T>(kind: "license" | "insurance"): Promise<ScanResponse<T>> {
  const response = await fetch(`/api/camera/scan/${kind}`, { method: "POST" });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `Camera service returned ${response.status}.`);
  }
  if (!payload.success) {
    throw new Error(payload.recommendation || "The document could not be read. Reposition it and try again.");
  }
  return payload as ScanResponse<T>;
}

async function printRegistration(payload: RegistrationPrintPayload): Promise<void> {
  const response = await fetch("/api/print/registration", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok || !result.success) {
    throw new Error(result.error || "The registration mockup could not be printed.");
  }
}

// The actual amount charged on the card reader, in cents. Keep this in
// sync with the fee breakdown shown in Step5 and the amount sent to
// the print service below.
const RENEWAL_FEE_CENTS = 7100; // $71.00

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
}: {
  data: PersonalInfo;
  onChange: (d: PersonalInfo) => void;
  onLicenseScan: (fields: LicenseScanFields) => void;
}) {
  const [scanned, setScanned] = useState(false);
  const set = (k: keyof PersonalInfo) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    onChange({ ...data, [k]: e.target.value });

  const handleScan = async (file?: File) => {
    const result = file
      ? await scanDocument<LicenseScanFields>("license", file)
      : await scanPiCamera<LicenseScanFields>("license");
    const fields = result.fields;
    onChange({
      ...data,
      firstName: fields.customer_first_name || data.firstName,
      lastName: fields.customer_last_name || data.lastName,
      dateOfBirth: fields.date_of_birth || data.dateOfBirth,
      address: fields.street_address || data.address,
      city: fields.city || data.city,
      state: STATE_NAMES[fields.state?.toUpperCase() || ""] || fields.state || data.state,
      zip: fields.zip_code || data.zip,
    });
    onLicenseScan(fields);
    setScanned(true);
  };

  return (
    <div className="flex flex-col gap-6">
      <ScanPrompt
        title="Scan Your Driver's License"
        subtitle="We'll extract your name, address, and date of birth automatically. You'll just need to add your email and phone."
        docType="license"
        scanned={scanned}
        onScan={handleScan}
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
      {scanned && (
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

type ScanState = "idle" | "scanning" | "done" | "error";

function ScanPrompt({
  title,
  subtitle,
  docType,
  scanned,
  onScan,
}: {
  title: string;
  subtitle: string;
  docType: "license" | "insurance";
  scanned: boolean;
  onScan: (file?: File) => Promise<void>;
}) {
  const [state, setState] = useState<ScanState>(scanned ? "done" : "idle");
  const [preview, setPreview] = useState<string | null>(null);
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const handleFile = async (file: File) => {
    if (!file.type.startsWith("image/")) {
      setError("Please select a JPG, PNG, or other image file.");
      setState("error");
      return;
    }
    setState("scanning");
    setError("");
    const url = URL.createObjectURL(file);
    setPreview(url);
    try {
      await onScan(file);
      setState("done");
    } catch (scanError) {
      setError(scanError instanceof Error ? scanError.message : "Scan failed. Please try again.");
      setState("error");
    }
  };

  const handleCamera = async () => {
    setState("scanning");
    setError("");
    setPreview(null);
    try {
      await onScan();
      setState("done");
    } catch (scanError) {
      setError(scanError instanceof Error ? scanError.message : "Camera scan failed. Please try again.");
      setState("error");
    }
  };

  const iconPath =
    docType === "license"
      ? "M2 5a2 2 0 012-2h12a2 2 0 012 2v8a2 2 0 01-2 2H4a2 2 0 01-2-2V5zm3 2h3v3H5V7zm5 0h4M5 12h8"
      : "M4 4h16v14H4V4zm4 4h8M4 10h16M8 14h5";

  return (
    <div
      className="rounded-xl overflow-hidden transition-all duration-300"
      style={{ border: `2px ${state === "done" ? "solid" : "dashed"} ${state === "done" ? "#3a7d44" : "var(--border)"}`, backgroundColor: state === "done" ? "#f0f7ee" : "var(--card)" }}
    >
      <input
        ref={fileRef}
        type="file"
        accept="image/*"
        capture="environment"
        className="hidden"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f); }}
      />

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
            <p className="text-xs leading-relaxed" style={{ color: "var(--muted-foreground)" }}>{subtitle}</p>
          </div>
          <button
            onClick={handleCamera}
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
            Capture & Scan
          </button>
          <button
            onClick={() => fileRef.current?.click()}
            className="text-xs underline underline-offset-2"
            style={{ color: "var(--muted-foreground)" }}
          >
            Upload an image instead
          </button>
        </div>
      )}

      {state === "scanning" && (
        <div className="flex flex-col items-center gap-4 px-6 py-8 relative overflow-hidden">
          {preview && (
            <div className="w-full max-h-36 overflow-hidden rounded-lg opacity-50" style={{ border: "1px solid var(--border)" }}>
              <img src={preview} alt="Scanning" className="w-full object-cover" />
            </div>
          )}
          <div className="flex items-center gap-3">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" className="animate-spin" style={{ color: "var(--primary)" }}>
              <circle cx="10" cy="10" r="8" stroke="currentColor" strokeWidth="2" strokeOpacity="0.2" />
              <path d="M10 2a8 8 0 018 8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
            <span className="text-sm font-medium" style={{ color: "var(--primary)" }}>Scanning document…</span>
          </div>
          {/* scan line animation */}
          <style>{`@keyframes scanline { 0%{top:10%} 100%{top:90%} }`}</style>
          <div className="absolute inset-x-6 top-4 bottom-4 pointer-events-none rounded-lg overflow-hidden" style={{ border: "1px solid rgba(26,58,92,0.15)" }}>
            <div className="absolute inset-x-0 h-0.5" style={{ backgroundColor: "var(--accent)", opacity: 0.8, animation: "scanline 1.2s ease-in-out infinite alternate", top: "50%" }} />
          </div>
        </div>
      )}

      {state === "done" && (
        <div className="flex items-center gap-4 px-5 py-4">
          {preview ? (
            <div className="w-16 h-10 rounded-md overflow-hidden shrink-0" style={{ border: "1px solid #b8ddb4" }}>
              <img src={preview} alt="Scanned" className="w-full h-full object-cover" />
            </div>
          ) : (
            <div className="w-10 h-10 rounded-full flex items-center justify-center shrink-0" style={{ backgroundColor: "#3a7d44" }}>
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                <path d="M3 8l3.5 3.5 6.5-6.5" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </div>
          )}
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold" style={{ color: "#2d6636" }}>Document scanned successfully</p>
            <p className="text-xs truncate" style={{ color: "#3a7d44" }}>Fields below have been pre-filled — review and correct as needed.</p>
          </div>
          <button
            onClick={() => { setState("idle"); setPreview(null); }}
            className="text-xs underline underline-offset-2 shrink-0"
            style={{ color: "var(--muted-foreground)" }}
          >
            Rescan
          </button>
        </div>
      )}

      {state === "error" && (
        <div className="flex flex-col items-center gap-3 px-6 py-6 text-center">
          <p className="text-sm font-semibold" style={{ color: "#b42318" }}>Document scan failed</p>
          <p className="text-xs" style={{ color: "var(--muted-foreground)" }}>{error}</p>
          <button
            onClick={() => { setState("idle"); setError(""); setPreview(null); fileRef.current?.click(); }}
            className="px-5 py-2.5 rounded-lg text-sm font-semibold"
            style={{ backgroundColor: "var(--accent)", color: "#ffffff" }}
          >
            Try Again
          </button>
        </div>
      )}
    </div>
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
  vehicle,
  onVehicleChange,
  onVehicleScan,
}: {
  data: InsuranceInfo;
  onChange: (d: InsuranceInfo) => void;
  vehicle: DriversLicense;
  onVehicleChange: (d: DriversLicense) => void;
  onVehicleScan: (fields: InsuranceScanFields) => void;
}) {
  const [insuranceScanned, setInsuranceScanned] = useState(false);
  const set = (k: keyof InsuranceInfo) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    onChange({ ...data, [k]: e.target.value });
  const setVehicle = (k: keyof DriversLicense) => (e: React.ChangeEvent<HTMLInputElement>) =>
    onVehicleChange({ ...vehicle, [k]: e.target.value });

  const handleInsuranceScan = async (file?: File) => {
    const result = file
      ? await scanDocument<InsuranceScanFields>("insurance", file)
      : await scanPiCamera<InsuranceScanFields>("insurance");
    const fields = result.fields;
    onChange({
      ...data,
      endDate: fields.insurance_expiration || data.endDate,
    });
    onVehicleScan(fields);
    setInsuranceScanned(true);
  };

  return (
    <div className="flex flex-col gap-6">
      <ScanPrompt
        title="Scan Proof of Insurance"
        subtitle="Place your insurance card or declaration page under the scanner to auto-fill the fields below."
        docType="insurance"
        scanned={insuranceScanned}
        onScan={handleInsuranceScan}
      />
      <div
        className="rounded-lg p-5 flex flex-col gap-5"
        style={{ border: "1px solid var(--border)", backgroundColor: "var(--card)" }}
      >
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--muted-foreground)" }}>Vehicle Information</p>
          <p className="text-xs mt-1" style={{ color: "var(--muted-foreground)" }}>Filled automatically from the insurance scan when available. Review and complete any missing fields.</p>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-5">
          <Field label="Year" required>
            <InputEl placeholder="2019" value={vehicle.vehicleYear} onChange={setVehicle("vehicleYear")} maxLength={4} />
          </Field>
          <Field label="Make" required>
            <InputEl placeholder="Toyota" value={vehicle.vehicleMake} onChange={setVehicle("vehicleMake")} />
          </Field>
          <Field label="Model" required>
            <InputEl placeholder="Camry" value={vehicle.vehicleModel} onChange={setVehicle("vehicleModel")} />
          </Field>
        </div>
        <Field label="VIN" required hint="17-character Vehicle Identification Number">
          <InputEl placeholder="1HGBH41JXMN109186" value={vehicle.vin} onChange={setVehicle("vin")} maxLength={17} />
        </Field>
        <Field label="Current License Plate" required>
          <InputEl placeholder="ABC-1234" value={vehicle.licensePlate} onChange={setVehicle("licensePlate")} />
        </Field>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
        <Field label="Insurance Provider" required>
          <SelectEl value={data.provider} onChange={set("provider")}>
            <option value="">Select provider</option>
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
        <Field label="Policy Start Date" required>
          <InputEl type="date" value={data.startDate} onChange={set("startDate")} />
        </Field>
        <Field label="Policy End Date" required>
          <InputEl type="date" value={data.endDate} onChange={set("endDate")} />
        </Field>
      </div>
      <Field label="Coverage Type" required>
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

function Step5({ amountCents }: { amountCents: number }) {
  const [readerStatus, setReaderStatus] = useState<"connecting" | "ready" | "error">("connecting");
  const [readerError, setReaderError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setReaderStatus("connecting");
    connectToReader()
      .then(() => {
        if (!cancelled) setReaderStatus("ready");
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setReaderStatus("error");
          setReaderError(error instanceof Error ? error.message : "Could not connect to the card reader.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const statusColor =
    readerStatus === "ready" ? "#1b7a4c" : readerStatus === "error" ? "#b42318" : "#c9a227";
  const statusText =
    readerStatus === "connecting"
      ? "Connecting to card reader..."
      : readerStatus === "ready"
      ? "Reader ready — tap, insert, or swipe"
      : readerError || "Could not connect to the card reader.";

  return (
    <div className="flex flex-col gap-6">
      {/* Test reader notice */}
      <div className="rounded-lg p-4 flex gap-3" style={{ backgroundColor: "#fffbea", border: "1px solid #f5d76e" }}>
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" className="mt-0.5 shrink-0">
          <path d="M9 2L16 15H2L9 2z" stroke="#b45309" strokeWidth="1.5" strokeLinejoin="round" fill="none" />
          <path d="M9 7v4M9 12.5h.01" stroke="#b45309" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
        <div>
          <p className="text-sm font-semibold" style={{ color: "#b45309" }}>Demo Mode — Simulated Reader</p>
          <p className="text-xs mt-0.5" style={{ color: "#92400e" }}>
            No physical card needed yet. Selecting "Pay &amp; Print" below runs a real (test-mode) Stripe charge against a simulated card reader.
          </p>
        </div>
      </div>

      {/* Fee summary */}
      <div className="rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)" }}>
        <div className="px-5 py-3" style={{ backgroundColor: "var(--secondary)" }}>
          <p className="text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--primary)" }}>Fee Summary</p>
        </div>
        <div className="px-5 py-1">
          {[
            { label: "Registration Renewal Fee", value: "$51.75" },
            { label: "State Processing Fee", value: "$4.75" },
            { label: "County Road & Bridge", value: "$11.50" },
            { label: "Service Fee", value: "$3.00" },
          ].map((r) => (
            <div key={r.label} className="flex justify-between py-2.5" style={{ borderBottom: "1px solid var(--border)" }}>
              <span className="text-sm" style={{ color: "var(--muted-foreground)" }}>{r.label}</span>
              <span className="text-sm font-medium">{r.value}</span>
            </div>
          ))}
          <div className="flex justify-between py-3">
            <span className="text-sm font-semibold">Total Due</span>
            <span className="text-base font-bold" style={{ color: "var(--primary)" }}>${(amountCents / 100).toFixed(2)}</span>
          </div>
        </div>
      </div>

      {/* Reader status */}
      <div
        className="rounded-lg p-5 flex items-center gap-3"
        style={{
          border: `1.5px solid ${readerStatus === "error" ? "#f2b8b5" : "var(--border)"}`,
          backgroundColor: readerStatus === "ready" ? "#eef6f1" : "var(--card)",
        }}
      >
        <span className="shrink-0 rounded-full" style={{ width: 10, height: 10, backgroundColor: statusColor }} />
        <div>
          <p className="text-sm font-medium" style={{ color: "var(--foreground)" }}>{statusText}</p>
          {readerStatus === "ready" && (
            <p className="text-xs mt-0.5" style={{ color: "var(--muted-foreground)" }}>
              Select "Pay & Print" below once the customer presents their card.
            </p>
          )}
        </div>
      </div>

      {/* Security badges */}
      <div className="flex items-center justify-center gap-5 pt-2">
        {[
          { icon: "🔒", label: "PCI-Compliant Reader" },
          { icon: "🛡️", label: "Stripe Terminal" },
          { icon: "✓", label: "Card Never Touches This Screen" },
        ].map((b) => (
          <div key={b.label} className="flex items-center gap-1.5">
            <span className="text-sm">{b.icon}</span>
            <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>{b.label}</span>
          </div>
        ))}
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
        <EditableRow label="Start Date"    value={insurance.startDate}    onChange={si("startDate")} type="date" />
        <EditableRow label="End Date"      value={insurance.endDate}      onChange={si("endDate")} type="date" />
        <EditableRow label="Coverage Type" value={insurance.coverageType} onChange={si("coverageType")} options={["liability","collision","comprehensive","full","uninsured"]} />
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
            <img src="/RN_Logo.png" alt="Registration Nation" className="h-24 w-auto object-contain" />
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
  const [paymentStage, setPaymentStage] = useState<"idle" | "charging" | "printing">("idle");
  const [pendingPaymentIntentId, setPendingPaymentIntentId] = useState<string | null>(null);
  const [printError, setPrintError] = useState("");
  const [confirmationNumber, setConfirmationNumber] = useState("");

  const [personal, setPersonal] = useState<PersonalInfo>({
    firstName: "", lastName: "", dateOfBirth: "", email: "", phone: "",
    address: "", city: "", state: "", zip: "",
  });
  const [license, setLicense] = useState<DriversLicense>({
    licenseNumber: "", licenseState: "", expirationDate: "", licenseClass: "",
    vehicleYear: "", vehicleMake: "", vehicleModel: "", vin: "", licensePlate: "",
  });
  const [licensePrefilled, setLicensePrefilled] = useState(false);

  const handleLicenseScan = (fields: LicenseScanFields) => {
    setLicensePrefilled(true);
    setLicense((current) => ({
      ...current,
      licenseNumber: fields.license_number || current.licenseNumber,
      licenseState: STATE_NAMES[fields.state?.toUpperCase() || ""] || fields.state || current.licenseState,
      expirationDate: fields.license_expiration || current.expirationDate,
      licenseClass: fields.license_class || current.licenseClass,
    }));
  };

  const handleInsuranceVehicleScan = (fields: InsuranceScanFields) => {
    setLicense((current) => ({
      ...current,
      vehicleYear: fields.vehicle_year || current.vehicleYear,
      vehicleMake: fields.vehicle_make || current.vehicleMake,
      vehicleModel: fields.vehicle_model || current.vehicleModel,
      vin: fields.vehicle_vin || current.vin,
    }));
  };
  const [insurance, setInsurance] = useState<InsuranceInfo>({
    provider: "", policyNumber: "", policyHolder: "", startDate: "",
    endDate: "", coverageType: "", agentName: "", agentPhone: "",
  });

  const stepTitles: Record<Step, string> = {
    1: "Personal Information",
    2: "Driver's License Details",
    3: "Insurance & Vehicle",
    4: "Review & Confirm",
    5: "Payment",
  };

  const stepSubtitles: Record<Step, string> = {
    1: "Scan your driver's license — we'll fill in the rest. Just add your email and phone.",
    2: "Confirm the details extracted from your driver's license.",
    3: "Scan your insurance document to fill vehicle details, then review the insurance information.",
    4: "Review all information before proceeding to payment.",
    5: "Tap, insert, or swipe your card to complete the $71.00 renewal payment.",
  };

  const handlePaymentAndPrint = async () => {
    setPrintError("");

    // If a previous attempt already charged the card and only printing
    // failed, reuse that charge instead of charging again - otherwise
    // a print retry would double-charge the customer.
    let paymentIntentId = pendingPaymentIntentId;

    if (!paymentIntentId) {
      setPaymentStage("charging");
      const chargeResult = await chargeCard(RENEWAL_FEE_CENTS, personal.email || undefined);
      if (!chargeResult.success) {
        setPrintError(chargeResult.error);
        setPaymentStage("idle");
        return;
      }
      paymentIntentId = chargeResult.paymentIntentId;
      setPendingPaymentIntentId(paymentIntentId);
    }

    const confirmation = `REG-${Math.random().toString(36).slice(2, 8).toUpperCase()}`;
    setPaymentStage("printing");
    try {
      await printRegistration({
        registrant: `${personal.firstName} ${personal.lastName}`.trim(),
        license_plate: license.licensePlate,
        vehicle_year: license.vehicleYear,
        vehicle_make: license.vehicleMake,
        vehicle_model: license.vehicleModel,
        vin: license.vin,
        amount: "$71.00",
        confirmation_number: confirmation,
      });
      setConfirmationNumber(confirmation);
      setSubmitted(true);
      setPendingPaymentIntentId(null); // this renewal is fully done

      // Log the completed renewal to Monday.com. This runs after the
      // customer-facing flow is already done - a logging hiccup here
      // shouldn't hold up the customer or look like the payment
      // failed, since the payment already succeeded.
      createMondayRecord(`${license.licensePlate} renewal`, {
        plate: license.licensePlate,
        registrant: `${personal.firstName} ${personal.lastName}`.trim(),
        amount: RENEWAL_FEE_CENTS,
        confirmationNumber: confirmation,
        paymentIntentId,
      }).then((mondayResult) => {
        if (mondayResult.success) {
          console.log(`Logged to Monday.com: item ${mondayResult.itemId}`);
        } else {
          console.warn(`Monday.com logging skipped: ${mondayResult.error}`);
        }
      });
    } catch (error) {
      // The card was already charged - pendingPaymentIntentId stays
      // set so the next click retries only the print, not the charge.
      setPrintError(error instanceof Error ? error.message : "Printing failed. Please try again.");
    } finally {
      setPaymentStage("idle");
    }
  };

  if (!authed) return <AdminLogin onLogin={() => setAuthed(true)} />;

  if (submitted) {
    const confNum = confirmationNumber;
    return (
      <div className="min-h-screen flex flex-col" style={{ backgroundColor: "var(--background)" }}>
        {/* Header */}
        <header style={{ backgroundColor: "#ffffff", borderBottom: "3px solid var(--accent)" }}>
          <div className="max-w-2xl mx-auto px-6 py-3 flex items-center justify-between">
            <img src="/RN_Logo.png" alt="Registration Nation" className="h-16 w-auto object-contain" />
            <div className="text-right">
              <p className="text-xs font-semibold tracking-wider uppercase" style={{ color: "var(--muted-foreground)" }}>Texas Tag & Title</p>
              <p className="text-sm font-medium" style={{ color: "var(--primary)" }}>Vehicle Registration Renewal</p>
            </div>
          </div>
        </header>

        <div className="flex-1 flex items-center justify-center px-6 py-12">
          <div className="max-w-lg w-full flex flex-col items-center gap-8">

            {/* Printing graphic */}
            <div className="relative flex flex-col items-center">
              <style>{`
                @keyframes printSlide { 0%{transform:translateY(-8px);opacity:0} 60%{opacity:1} 100%{transform:translateY(0);opacity:1} }
                @keyframes pulse-ring { 0%,100%{opacity:0.4;transform:scale(1)} 50%{opacity:0.15;transform:scale(1.15)} }
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
                      {[`${personal.firstName} ${personal.lastName}`, license.licensePlate, "$71.00"].map((line, i) => (
                        <div key={i} className="h-px w-full rounded" style={{ backgroundColor: i === 2 ? "var(--accent)" : "var(--border)", marginTop: i === 2 ? "3px" : undefined }} />
                      ))}
                      <p style={{ fontSize: "5px", color: "var(--muted-foreground)" }}>PAID · {new Date().toLocaleDateString()}</p>
                    </div>
                  </div>
                </div>
              </div>

              {/* Glow ring */}
              <div className="absolute rounded-full" style={{ width: 160, height: 160, top: "50%", left: "50%", transform: "translate(-50%,-50%)", backgroundColor: "var(--accent)", opacity: 0.08, animation: "pulse-ring 2s ease-in-out infinite" }} />
            </div>

            {/* Status text */}
            <div className="text-center flex flex-col gap-2">
              <h2 className="text-3xl" style={{ color: "var(--foreground)" }}>Printing Now</h2>
              <p className="text-sm" style={{ color: "var(--muted-foreground)" }}>
                Payment verified — your registration sticker and receipt are printing. Please collect them from the printer.
              </p>
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
                  { label: "Amount Paid", value: "$71.00" },
                  { label: "Confirmation #", value: confNum },
                ].map((r) => (
                  <div key={r.label} className="flex justify-between items-center py-2.5" style={{ borderBottom: "1px solid var(--border)" }}>
                    <span className="text-sm" style={{ color: "var(--muted-foreground)" }}>{r.label}</span>
                    <span className="text-sm font-semibold" style={{ color: r.label === "Amount Paid" ? "#3a7d44" : "var(--foreground)" }}>{r.value || "—"}</span>
                  </div>
                ))}
              </div>
            </div>

            <button
              onClick={() => { setSubmitted(false); setConfirmationNumber(""); setPrintError(""); setPendingPaymentIntentId(null); setStep(1); setLicensePrefilled(false); setPersonal({ firstName: "", lastName: "", dateOfBirth: "", email: "", phone: "", address: "", city: "", state: "", zip: "" }); setLicense({ licenseNumber: "", licenseState: "", expirationDate: "", licenseClass: "", vehicleYear: "", vehicleMake: "", vehicleModel: "", vin: "", licensePlate: "" }); setInsurance({ provider: "", policyNumber: "", policyHolder: "", startDate: "", endDate: "", coverageType: "", agentName: "", agentPhone: "" }); }}
              className="px-8 py-3 rounded-lg text-sm font-semibold transition-all duration-150"
              style={{ backgroundColor: "var(--primary)", color: "var(--primary-foreground)" }}
              onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = "#142e4a"; }}
              onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = "var(--primary)"; }}
            >
              Start Next Customer
            </button>
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
            src="/RN_Logo.png"
            alt="Registration Nation"
            className="h-16 w-auto object-contain"
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
          {step === 1 && <Step1 data={personal} onChange={setPersonal} onLicenseScan={handleLicenseScan} />}
          {step === 2 && <Step2 data={license} onChange={setLicense} />}
          {step === 3 && <Step3 data={insurance} onChange={setInsurance} vehicle={license} onVehicleChange={setLicense} onVehicleScan={handleInsuranceVehicleScan} />}
          {step === 4 && <Step4 personal={personal} license={license} insurance={insurance} onPersonalChange={setPersonal} onLicenseChange={setLicense} onInsuranceChange={setInsurance} />}
          {step === 5 && <Step5 amountCents={RENEWAL_FEE_CENTS} />}
        </div>

        <div className="flex items-center justify-between">
          {step > 1 ? (
            <button
              onClick={() => setStep((s) => (s - 1) as Step)}
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
              onClick={handlePaymentAndPrint}
              disabled={paymentStage !== "idle"}
              className="flex items-center gap-2 text-sm font-semibold px-6 py-2.5 rounded-md transition-all duration-150"
              style={{ backgroundColor: "#3a7d44", color: "#ffffff", opacity: paymentStage !== "idle" ? 0.7 : 1 }}
              onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = "#2d6636"; }}
              onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = "#3a7d44"; }}
            >
              {paymentStage === "charging"
                ? "Processing Payment..."
                : paymentStage === "printing"
                ? "Sending to Printer..."
                : "Pay $71.00 & Print"}
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <path d="M2 7h10M8 3l4 4-4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          )}
        </div>

        {printError && (
          <p className="text-sm text-right mt-3" role="alert" style={{ color: "#b42318" }}>
            {printError}
          </p>
        )}

        <p className="text-xs text-center mt-8" style={{ color: "var(--muted-foreground)" }}>
          Fields marked with <span style={{ color: "#e05a1a" }}>*</span> are required. Your information is encrypted and transmitted securely.
        </p>
      </main>
    </div>
  );
}
