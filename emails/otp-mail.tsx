import {
  Body,
  Button,
  Column,
  Container,
  Font,
  Head,
  Html,
  Img,
  Link,
  Preview,
  Row,
  Section,
  Tailwind,
  Text,
} from "@react-email/components";
import * as React from "react";

const baseUrl = process.env.VERCEL_URL
  ? `https://${process.env.VERCEL_URL}`
  : "";

export const BasicAxiosMail = () => (
  <Html>
    <Head>
      <Font
        fontFamily="Lato"
        fallbackFontFamily="sans-serif"
        webFont={{
          url: "https://fonts.gstatic.com/s/lato/v24/S6uyw4BMUTPHjxAwXjeu.woff2",
          format: "woff2",
        }}
        fontWeight="400"
        fontStyle="normal"
      />
      <meta name="color-scheme" content="light" />
      <meta name="supported-color-schemes" content="light" />
    </Head>
    <Preview>Your mail verification OTP: OTP</Preview>
    <Tailwind>
      <Body className="bg-white my-auto mx-auto font-sans px-2">
        <Container
          className={`bg-[url('https://public.amcspsgtech.in/content/bg.png')] bg-contain rounded-xl my-[40px] mx-auto`}
        >
          <Section className="p-10">
            <Section>
              <Row>
                <Column align="center">
                  <Link href="https://axios.amcspsgtech.in">
                    <Img
                      src="https://public.amcspsgtech.in/content/axios.png"
                      className=""
                    />
                  </Link>
                </Column>
              </Row>
            </Section>
            <Section className="my-10">
              <Text className="text-white text-justify text-lg">
                Please use the verification code below to verify your mail id.
              </Text>
              <Text className="text-[#e2a52a] text-justify text-4xl font-bold">
                OTP
              </Text>
              <Text className="text-white text-justify text-lg">
                If you didn't request this, you can ignore this email.
              </Text>
              <Text className="text-white text-justify text-base">
                With Regards,
                <br /> Axios Team
              </Text>
            </Section>
            <Section>
              <Row>
                <Column align="right">
                  <Link href="https://www.instagram.com/axios.psgtech">
                    <Img src="https://public.amcspsgtech.in/content/instagram-icon.png" />
                  </Link>
                </Column>
                <Column align="left">
                  <Link href="https://links.amcspsgtech.in">
                    <Img src="https://public.amcspsgtech.in/content/linktree-icon.png" />
                  </Link>
                </Column>
              </Row>
              <Text className="text-white text-center">
                Copyright © 2024, Computational Sciences Association
                <br /> All rights reserved
              </Text>
            </Section>
          </Section>
        </Container>
      </Body>
    </Tailwind>
  </Html>
);

export default BasicAxiosMail;
