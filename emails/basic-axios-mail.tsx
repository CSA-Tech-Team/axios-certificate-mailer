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
    <Preview>asjhdhjh</Preview>
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
                <b>Dear MrRhuezzler</b>,
                <br /> <br /> Lorem ipsum dolor sit amet consectetur,
                adipisicing elit. Sint eveniet sequi voluptate amet labore eaque
                quisquam quidem iusto, culpa sunt sed ratione excepturi delectus
                molestiae, in nostrum ea, quod odit. Lorem ipsum dolor sit amet
                consectetur, adipisicing elit. Sint eveniet sequi voluptate amet
                labore eaque quisquam quidem iusto, culpa sunt sed ratione
                excepturi delectus molestiae, in nostrum ea, quod odit. Lorem
                ipsum dolor sit amet consectetur, adipisicing elit. Sint eveniet
                sequi voluptate amet labore eaque quisquam quidem iusto, culpa
                sunt sed ratione excepturi delectus molestiae, in nostrum ea,
                quod odit. Lorem ipsum dolor sit amet consectetur, adipisicing
                elit. Sint eveniet sequi voluptate amet labore eaque quisquam
                quidem iusto, culpa sunt sed ratione excepturi delectus
                molestiae, in nostrum ea, quod odit. Lorem ipsum dolor sit amet
                consectetur, adipisicing elit. Sint eveniet sequi voluptate amet
                labore eaque quisquam quidem iusto, culpa sunt sed ratione
                excepturi delectus molestiae, in nostrum ea, quod odit. Lorem
                ipsum dolor sit amet consectetur, adipisicing elit. Sint
                evenietz
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
                  <Link href="https://linktr.ee/axios.psgtech">
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
