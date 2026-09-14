import { NextRequest, NextResponse } from "next/server";

// Fail closed: without CONSOLE_ACCESS_TOKEN configured nothing is served.
export function middleware(req: NextRequest) {
  const expected = process.env.CONSOLE_ACCESS_TOKEN;
  if (!expected) return new NextResponse("console not configured", { status: 503 });
  const url = req.nextUrl;
  const presented = url.searchParams.get("token") ?? req.cookies.get("console_token")?.value;
  if (presented !== expected) return new NextResponse("unauthorised", { status: 401 });
  const res = NextResponse.next();
  if (url.searchParams.get("token")) {
    res.cookies.set("console_token", expected, { httpOnly: true, sameSite: "strict", secure: true, path: "/" });
  }
  return res;
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
