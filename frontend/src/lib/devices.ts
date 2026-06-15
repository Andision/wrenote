// Audio input device enumeration for the microphone picker.
// Device labels are only populated after mic permission has been granted;
// before that the OS hides them (we fall back to a generic label in the UI).

export async function listAudioInputs(): Promise<MediaDeviceInfo[]> {
  if (typeof navigator === "undefined" || !navigator.mediaDevices?.enumerateDevices) {
    return [];
  }
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    return devices.filter((d) => d.kind === "audioinput");
  } catch {
    return [];
  }
}
