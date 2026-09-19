export function LayerToggle({ checked, disabled, label, description, onChange, testId }: { checked: boolean; disabled?: boolean; label: string; description: string; onChange: (checked: boolean) => void; testId?: string }) {
  const id = testId ?? label.toLowerCase().replace(/\s+/g, "-");
  return <label className={`layer-row${disabled ? " disabled" : ""}`} htmlFor={id}><span><strong>{label}</strong><small>{description}</small></span><span className="switch"><input id={id} type="checkbox" checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} data-testid={testId} /><span aria-hidden="true" /></span></label>;
}
