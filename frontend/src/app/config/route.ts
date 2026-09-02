import { NextResponse } from 'next/server'

/**
 * Runtime configuration endpoint.
 *
 * Native installs serve the browser and API through the Next.js origin.  An
 * empty apiUrl makes the client use `/api/*`, which Next.js rewrites to the
 * private FastAPI listener.  This avoids exposing :5055 to the browser and
 * removes the previous Host-header-derived redirect surface.
 *
 * API_URL/NEXT_PUBLIC_API_URL remain explicit escape hatches for deployments
 * that intentionally expose the backend separately.
 */
export async function GET() {
  const explicitApiUrl = process.env.API_URL || process.env.NEXT_PUBLIC_API_URL

  return NextResponse.json({
    apiUrl: explicitApiUrl || '',
  })
}
