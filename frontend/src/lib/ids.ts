const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Ids in URLs are UUIDs; anything else is answered with "not found" before calling the API. */
export function isUuid(value: string): boolean {
  return UUID.test(value);
}
